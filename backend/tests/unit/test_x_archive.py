"""Historical requests preserve dates, caps, and endpoint-specific cursors."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from sentiment_prep.errors import ExternalServiceError, ValidationError
from sentiment_prep.models import CollectionProgress, Dataset
from sentiment_prep.sources.spend_guard import InMemoryLedger, SpendGuard
from sentiment_prep.sources.x_search import XSearchSource, clamp_window


def make_guard(max_reads=1000):
    return SpendGuard(InMemoryLedger(), max_reads, 3000, 0.005)


@pytest.mark.parametrize("days,endpoint", [(1, "recent"), (3, "recent"), (12, "all"), (365, "all")])
def test_automatically_routes_dates_without_shortening_window(days, endpoint):
    start = datetime.now(UTC) - timedelta(days=days)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [], "meta": {}})

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://x.test/2"
    ) as client:
        dataset = XSearchSource(make_guard(), client, start_time=start).fetch(20, "iphone")
    assert requests[0].url.path == f"/2/tweets/search/{endpoint}"
    assert requests[0].url.params["start_time"] == start.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert dataset.window_start == start


def test_archive_cursor_and_accounting_survive_resume():
    start = datetime(2020, 9, 9, tzinfo=UTC)
    end = datetime(2020, 9, 21, tzinfo=UTC)
    requests = []
    saved = []

    def handler(request):
        requests.append(request)
        page = len(requests)
        return httpx.Response(
            200,
            json={
                "data": [{"id": str(page), "text": "This post has enough words to keep"}],
                "meta": {"next_token": "archive-page-2"} if page == 1 else {},
            },
        )

    def persist(dataset, progress):
        saved.append((dataset.model_copy(deep=True), progress.model_copy(deep=True)))

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://x.test/2"
    ) as client:
        source = XSearchSource(make_guard(), client, start_time=start, end_time=end)
        source.fetch(
            20,
            "iphone",
            should_stop=lambda: len(requests) == 1,
            progress=CollectionProgress(request={}, billed_reads=0, committed_cost_usd=Decimal(0)),
            persist=persist,
        )
        dataset, progress = saved[-1]
        assert progress.search_mode == "all"
        assert progress.next_token == "archive-page-2"
        # A fresh adapter without dates must restore the saved endpoint and bounds.
        XSearchSource(make_guard(), client).fetch(
            20, "iphone", resume=dataset, progress=progress, persist=persist
        )
    assert [r.url.path for r in requests] == ["/2/tweets/search/all"] * 2
    assert requests[1].url.params["next_token"] == "archive-page-2"
    assert requests[0].url.params["start_time"] == requests[1].url.params["start_time"]
    assert requests[1].url.params["end_time"] == "2020-09-21T00:00:00Z"
    assert len(saved[-1][0].records) == 2
    assert saved[-1][1].billed_reads == 2
    assert saved[-1][1].committed_cost_usd == Decimal("0.010")


@pytest.mark.parametrize("mode", [None, "recent"])
def test_expired_recent_cursor_is_never_sent_to_archive(mode):
    start = datetime.now(UTC) - timedelta(days=8)
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda r: pytest.fail("must not call X"))
        ) as client,
        pytest.raises(ValidationError, match="expired"),
    ):
        XSearchSource(make_guard(), client, start_time=start).fetch(
            20,
            "iphone",
            resume=Dataset(records=[], source_type="x", window_start=start),
            progress=CollectionProgress(request={}, next_token="recent-cursor", search_mode=mode),
        )


def test_archive_access_failure_explains_permissions_and_releases_reservation():
    ledger = InMemoryLedger()
    guard = SpendGuard(ledger, 1000, 3000, 0.005)
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(403)),
            base_url="https://x.test/2",
        ) as client,
        pytest.raises(ExternalServiceError, match="pay-per-use"),
    ):
        XSearchSource(guard, client, start_time=datetime(2020, 1, 1, tzinfo=UTC)).fetch(
            20, "iphone"
        )
    assert ledger.get(guard.today()) == 0


def test_archive_cap_prevents_network_call():
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("cap must stop call"))
    ) as client:
        result = XSearchSource(
            make_guard(5), client, start_time=datetime(2020, 1, 1, tzinfo=UTC)
        ).fetch(20, "iphone")
    assert "per-fetch cap" in result.truncated_reason


def test_archive_paces_pages(monkeypatch):
    sleeps = []
    monkeypatch.setattr("sentiment_prep.sources.x_search.time.monotonic", lambda: 10.0)
    monkeypatch.setattr("sentiment_prep.sources.x_search.time.sleep", sleeps.append)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "data": [{"id": str(len(calls)), "text": "This post has enough words to keep"}],
                "meta": {"next_token": "next"} if len(calls) == 1 else {},
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://x.test/2"
    ) as client:
        XSearchSource(make_guard(), client, start_time=datetime(2020, 1, 1, tzinfo=UTC)).fetch(
            20, "iphone"
        )
    assert sleeps == [1.0]


@pytest.mark.parametrize(
    "start,end",
    [
        (datetime(2005, 1, 1, tzinfo=UTC), None),
        (None, datetime(2005, 1, 1, tzinfo=UTC)),
        (datetime.now(UTC) + timedelta(days=1), None),
        (datetime(2020, 1, 1), None),  # noqa: DTZ001 -- intentionally invalid client input
    ],
)
def test_invalid_windows_are_rejected_before_spending(start, end):
    with pytest.raises(ValidationError):
        clamp_window(start, end)
