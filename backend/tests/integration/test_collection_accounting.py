"""API summaries report settled reads and cost independently from retained records."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from sentiment_prep.api import deps, routes
from sentiment_prep.api.schemas import LoadRequest
from sentiment_prep.config import Settings
from sentiment_prep.history.services import DbLedger
from sentiment_prep.models import CollectionProgress, Dataset, DatasetBundle, Record
from sentiment_prep.sources.spend_guard import InMemoryLedger, SpendGuard
from sentiment_prep.sources.x_search import XSearchSource
from sentiment_prep.storage.checkpoints import LocalCheckpointStore
from sentiment_prep.storage.repository import InMemoryRepository


def test_recovers_476_post_job_without_replaying_paid_pages(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, dedupe_enabled=True)
    body = LoadRequest(
        source="x",
        query="iphone",
        limit=500,
        request_id="legacy-476",
        start_time=datetime(2020, 9, 9, tzinfo=UTC),
    )
    rows = [
        Record(id=str(i), text=f"Opinion number {i} has several useful words", source_type="x")
        for i in range(476)
    ]
    rows[0].label = "positive"
    repo = InMemoryRepository()
    repo.save(
        DatasetBundle(
            dataset_id="x-legacy-476",
            original=Dataset(
                records=rows,
                source_type="x",
                query="iphone",
                window_start=body.start_time,
                window_end=datetime(2020, 9, 21, tzinfo=UTC),
                filtered_out={"duplicate": 20, "near_duplicate": 4},
            ),
            collection=CollectionProgress(
                request=body.model_dump(mode="json", exclude={"request_id"}),
                next_token="unread-page",
                search_mode="all",
                reads=508,
                billed_reads=508,
                committed_cost_usd=Decimal("2.540"),
                complete=True,
            ),
        )
    )
    old_summary = routes._summary(repo.get("x-legacy-476"))
    assert old_summary.partial and "resume" in old_summary.truncated_reason
    assert old_summary.resume_request_id == "legacy-476"
    assert "Resume collection" in " ".join(old_summary.warnings)
    requests = []

    def page(request):
        requests.append(request)
        assert request.url.params["next_token"] == "unread-page"
        assert request.url.params["max_results"] == "24"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": str(i), "text": f"Opinion number {i} has several useful words"}
                    for i in range(476, 500)
                ],
                "meta": {"next_token": "not-needed"},
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(page), base_url="https://offline.test/2"
    ) as client:
        monkeypatch.setattr(
            deps,
            "get_x_source",
            lambda *a: XSearchSource(
                SpendGuard(InMemoryLedger(), 1000, 3000, 0.005),
                client,
            ),
        )
        result = routes._collect_x(body, repo, LocalCheckpointStore(tmp_path), settings)
        assert result.record_count == 500 and not result.partial
        assert result.billed_reads == 532 and result.committed_cost_usd == pytest.approx(2.66)
        assert result.filtered_out == {"duplicate": 20, "near_duplicate": 4}
        assert result.labelled_count == 1
        again = routes._collect_x(body, repo, LocalCheckpointStore(tmp_path), settings)
        assert again.record_count == 500 and len(requests) == 1


@pytest.mark.parametrize("status", [200, 403])
def test_historical_collection_preserves_bounds_and_reports_access_errors(
    tmp_path, monkeypatch, status
):
    settings = Settings(_env_file=None, dedupe_enabled=False)
    repo = InMemoryRepository()
    requests = []
    start = datetime(2020, 9, 9, tzinfo=UTC)
    end = datetime.now(UTC) + timedelta(hours=1)

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"data": [], "meta": {}})

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://offline.test/2"
    ) as provider:

        def source(query, start_time, end_time):
            return XSearchSource(
                SpendGuard(DbLedger(0.005), 1000, 3000, 0.005),
                provider,
                start_time=start_time,
                end_time=end_time,
            )

        monkeypatch.setattr(deps, "get_x_source", source)
        result = routes._collect_x(
            LoadRequest(source="x", query="iphone", limit=20, start_time=start, end_time=end),
            repo,
            LocalCheckpointStore(tmp_path),
            settings,
        )
    assert requests[0].url.path == "/2/tweets/search/all"
    assert requests[0].url.params["start_time"] == "2020-09-09T00:00:00Z"
    assert result.window_start == start
    assert result.window_end < datetime.now(UTC)
    assert repo.get(result.dataset_id).collection.search_mode == "all"
    assert result.billed_reads == 0
    if status == 403:
        assert "pay-per-use" in result.truncated_reason
        assert result.partial


def test_legacy_collection_does_not_invent_zero_spend():
    bundle = DatasetBundle(
        dataset_id="x-old",
        original=Dataset(source_type="x", records=[]),
        collection=CollectionProgress.model_validate({"request": {}, "reads": 100}),
    )
    result = routes._summary(bundle)
    assert result.billed_reads is None
    assert result.committed_cost_usd is None


@pytest.mark.parametrize("limit", [20, 100])
def test_filtering_and_oversized_final_pages_report_committed_cost(tmp_path, monkeypatch, limit):
    settings = Settings(_env_file=None, x_cost_per_read_usd=0.017, dedupe_enabled=False)
    ledger = DbLedger(settings.x_cost_per_read_usd)
    guard = SpendGuard(ledger, 1000, 3000, settings.x_cost_per_read_usd)
    monkeypatch.setattr(guard, "today", lambda: f"2099-01-{limit // 10:02d}")
    posts = [
        {
            "id": str(i),
            "text": f"post {i} has enough useful words",
            "lang": "en" if i < 20 else "fr",
        }
        for i in range(100)
    ]
    provider = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": posts, "meta": {}})
        ),
        base_url="https://offline.test",
    )
    monkeypatch.setattr(
        deps, "get_x_source", lambda *args, **kwargs: XSearchSource(guard, provider)
    )
    repo = InMemoryRepository()
    result = routes._collect_x(
        LoadRequest(source="x", query="accounting", limit=limit, request_id=f"billing-{limit}"),
        repo,
        LocalCheckpointStore(tmp_path),
        settings,
        lambda: False,
    )
    assert result.record_count == 20
    assert result.billed_reads == 100
    assert result.committed_cost_usd == pytest.approx(1.7)
    assert ledger.get(guard.today()) == 100
    reloaded = routes._summary(repo.get(result.dataset_id))
    assert reloaded.committed_cost_usd == result.committed_cost_usd
    provider.close()
