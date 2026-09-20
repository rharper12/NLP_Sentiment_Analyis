from datetime import UTC, datetime, timedelta

import httpx
import pytest

from sentiment_prep.errors import ExternalServiceError, ValidationError
from sentiment_prep.history.services import DbLedger
from sentiment_prep.sources.csv_upload import CsvUploadSource
from sentiment_prep.sources.huggingface import HuggingFaceSource
from sentiment_prep.sources.spend_guard import (
    InMemoryLedger,
    SpendCapReachedError,
    SpendGuard,
)
from sentiment_prep.sources.x_search import XSearchSource


def post(i: int, lang: str = "en") -> dict:
    return {
        "id": str(i),
        "text": f"post number {i} is quite interesting today",
        "lang": lang,
        "created_at": "2026-09-01T10:00:00.000Z",
    }


def x_client(pages: list[httpx.Response]) -> httpx.Client:
    calls = iter(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        return next(calls)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.x.test/2")


_LEDGERS: dict[int, InMemoryLedger] = {}


def guard(per_fetch=1000, per_day=3000) -> SpendGuard:
    ledger = InMemoryLedger()
    g = SpendGuard(ledger, per_fetch, per_day, 0.005)
    _LEDGERS[id(g)] = ledger
    return g


def ledger_of(g: SpendGuard) -> InMemoryLedger:
    """The ledger a test guard was built with, so assertions go through the real interface."""
    return _LEDGERS[id(g)]


def test_x_paginates_until_limit():
    pages = [
        httpx.Response(
            200, json={"data": [post(i) for i in range(100)], "meta": {"next_token": "t2"}}
        ),
        httpx.Response(200, json={"data": [post(i) for i in range(100, 200)], "meta": {}}),
    ]
    ds = XSearchSource(guard(), x_client(pages)).fetch(150, query="lindsay clancy")
    assert len(ds.records) == 150
    assert ds.query.endswith("lang:en -is:retweet")
    assert ds.truncated_reason is None


def test_x_stops_at_last_page_with_reason():
    pages = [httpx.Response(200, json={"data": [post(i) for i in range(30)], "meta": {}})]
    ds = XSearchSource(guard(), x_client(pages)).fetch(600, query="q")
    assert len(ds.records) == 30
    assert "7 days" in ds.truncated_reason


def test_x_spend_cap_truncates_without_raising():
    pages = [
        httpx.Response(
            200, json={"data": [post(i) for i in range(100)], "meta": {"next_token": "t2"}}
        ),
        httpx.Response(
            200, json={"data": [post(i) for i in range(100, 200)], "meta": {"next_token": "t3"}}
        ),
    ]
    g = guard(per_fetch=150)
    ds = XSearchSource(g, x_client(pages)).fetch(600, query="q")
    assert len(ds.records) == 100
    assert "per-fetch cap" in ds.truncated_reason
    assert g.reads_this_fetch == 100


def test_x_retries_on_429(monkeypatch):
    monkeypatch.setattr("sentiment_prep.sources.x_search.time.sleep", lambda s: None)
    pages = [
        httpx.Response(429, headers={"x-rate-limit-reset": "0"}),
        httpx.Response(200, json={"data": [post(i) for i in range(10)], "meta": {}}),
    ]
    ds = XSearchSource(guard(), x_client(pages)).fetch(10, query="q")
    assert len(ds.records) == 10


def test_x_filters_short_and_non_english():
    data = [post(1), {"id": "2", "text": "too short", "lang": "en"}, post(3, lang="fr")]
    pages = [httpx.Response(200, json={"data": data, "meta": {}})]
    ds = XSearchSource(guard(), x_client(pages)).fetch(10, query="q")
    assert [r.id for r in ds.records] == ["1"]


def test_db_ledger_reservations_are_atomic_and_capped():
    """The cap is enforced by the database, not by a read-then-write in the application."""
    ledger = DbLedger(0.005, query="test")
    day = "2030-01-01"
    assert ledger.get(day) == 0

    assert ledger.reserve(day, 40, max_per_day=50) is True
    assert ledger.settle(day, reserved=40, actual=40, query="q") == 40

    # A second caller sees the first reservation and is refused rather than both squeezing in.
    assert ledger.reserve(day, 20, max_per_day=50) is False
    assert ledger.reserve(day, 10, max_per_day=50) is True
    assert DbLedger(0.005).get(day) == 40  # reserved, not yet billed


def test_db_ledger_releases_the_unused_part_of_a_reservation():
    """A page that returns fewer posts than requested must hand the difference back."""
    ledger = DbLedger(0.005)
    day = "2030-01-02"
    assert ledger.reserve(day, 100, max_per_day=100) is True
    assert ledger.reserve(day, 1, max_per_day=100) is False
    ledger.settle(day, reserved=100, actual=10, query="q")
    assert ledger.get(day) == 10
    # 90 reads went back into the day's budget, so the next reservation fits.
    assert ledger.reserve(day, 80, max_per_day=100) is True


def test_guard_refuses_when_the_ledger_refuses():
    g = guard(per_day=50)
    g.reserve(50)
    g.record(50)
    with pytest.raises(SpendCapReachedError):
        g.reserve(1)


def test_x_fetch_stops_when_cancelled():
    pages = [
        httpx.Response(
            200, json={"data": [post(i) for i in range(100)], "meta": {"next_token": "t2"}}
        ),
        httpx.Response(
            200, json={"data": [post(i) for i in range(100, 200)], "meta": {"next_token": "t3"}}
        ),
    ]
    calls = {"n": 0}

    def stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # allow the first page, cancel before the second

    ds = XSearchSource(guard(), x_client(pages)).fetch(600, query="q", should_stop=stop)
    assert len(ds.records) == 100 and ds.truncated_reason == "cancelled by client"


def test_huggingface_pages_and_maps_labels():
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        length = int(request.url.params["length"])
        rows = [
            {
                "row_idx": offset + i,
                "row": {"text": f"tweet {offset + i} about things", "label": i % 3},
            }
            for i in range(length)
        ]
        return httpx.Response(200, json={"rows": rows})

    src = HuggingFaceSource(
        "cardiffnlp/tweet_eval",
        "sentiment",
        "train",
        "text",
        "label",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://hf.test"),
    )
    ds = src.fetch(250)
    assert len(ds.records) == 250
    assert ds.records[1].label == "neutral"


def test_csv_upload_requires_text_column():
    with pytest.raises(ValidationError):
        CsvUploadSource(b"body,label\nhi,pos\n").fetch(10)
    ds = CsvUploadSource("\ufeffText,Label\nhello there,pos\n,neg\n".encode()).fetch(10)
    assert len(ds.records) == 1 and ds.records[0].label == "pos"


def test_x_upstream_failures_become_actionable_errors():
    """A bad token must not surface as a generic 500; the reservation is released either way."""
    cases = {401: "credentials", 402: "credit balance", 400: "search operators", 503: "unavailable"}
    for status, phrase in cases.items():
        g = guard()
        source = XSearchSource(g, x_client([httpx.Response(status, json={})]))
        with pytest.raises(ExternalServiceError) as caught:
            source.fetch(100, query="q")
        assert phrase in str(caught.value) and str(status) in str(caught.value)
        assert ledger_of(g).reserved(SpendGuard.today()) == 0  # nothing stranded


def test_release_returns_budget_to_the_day():
    """An unused reservation must not strand budget until midnight."""
    ledger = InMemoryLedger()
    guard_ = SpendGuard(ledger, max_per_fetch=1000, max_per_day=100, cost_per_read_usd=0.005)
    today = SpendGuard.today()
    guard_.reserve(100)
    assert ledger.reserved(today) == 100
    assert ledger.reserve(today, 1, max_per_day=100) is False  # budget is fully committed
    guard_.release()
    assert ledger.reserved(today) == 0
    assert ledger.reserve(today, 100, max_per_day=100) is True


def test_x_measures_length_by_content_not_by_links_and_mentions():
    """A post whose bulk is a link and a mention says nothing, and would be emptied by cleaning.

    Filtering it at collection rather than mid-pipeline keeps the record count identical across
    preprocessing configurations, so two runs are comparable.
    """
    link_only = {"id": "1", "text": "@someone check this out https://t.co/abcdef", "lang": "en"}
    real = {
        "id": "2",
        "text": "@someone this trial coverage has been relentless today",
        "lang": "en",
    }
    pages = [httpx.Response(200, json={"data": [link_only, real], "meta": {}})]

    ds = XSearchSource(guard(), x_client(pages)).fetch(10, query="q")

    assert [r.id for r in ds.records] == ["2"]
    assert ds.filtered_out == {"no_content_after_cleaning": 1}


def test_x_reports_why_posts_were_dropped():
    """The write-up needs the denominator: how many the source returned, not just how many kept."""
    data = [
        {"id": "1", "text": "a perfectly ordinary english post about things", "lang": "en"},
        {"id": "2", "text": "https://t.co/x @y", "lang": "en"},
        {"id": "3", "text": "un message parfaitement ordinaire au sujet des choses", "lang": "fr"},
    ]
    ds = XSearchSource(
        guard(), x_client([httpx.Response(200, json={"data": data, "meta": {}})])
    ).fetch(10, query="q")
    assert [r.id for r in ds.records] == ["1"]
    assert ds.filtered_out == {"no_content_after_cleaning": 1, "not_english": 1}


def test_search_window_is_sent_and_recorded():
    """The window is provenance: a write-up has to state the range the posts came from."""
    start = datetime(2026, 9, 9, tzinfo=UTC)
    end = datetime(2026, 9, 13, tzinfo=UTC)
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json={"data": [post(1)], "meta": {}})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.x.test/2")
    ds = XSearchSource(guard(), client, start_time=start, end_time=end).fetch(10, query="q")

    assert captured["start_time"].endswith("Z") and captured["end_time"].endswith("Z")
    assert ds.window_start is not None and ds.window_end is not None
    assert ds.truncated_reason == "no more matching posts in the selected window"


def test_window_is_clamped_to_what_recent_search_can_serve():
    """X rejects a start older than seven days or an end at the present instant; clamp, not fail."""
    from sentiment_prep.sources.x_search import clamp_window

    now = datetime.now(UTC)
    start, end = clamp_window(now - timedelta(days=30), now + timedelta(hours=1))

    assert start is not None and start > now - timedelta(days=7)
    assert end is not None and end < now
    assert clamp_window(None, None) == (None, None)


def test_a_backwards_window_is_refused_rather_than_clamped():
    """Clamping cannot repair it, and X would answer with a 400 blamed on the query syntax."""
    from datetime import UTC, datetime, timedelta

    from sentiment_prep.sources.x_search import clamp_window

    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="before its end"):
        clamp_window(now, now - timedelta(days=1))


@pytest.mark.parametrize("actual", [100, 20, 0])
def test_reservation_and_audit_settle_on_original_utc_day(monkeypatch, actual):
    from contextlib import contextmanager
    from datetime import date

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from sentiment_prep.history.models import Base, SpendDay, SpendEntry
    from sentiment_prep.history.services import DbLedger

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    @contextmanager
    def transaction():
        with Session(engine) as session, session.begin():
            yield session

    ledger = DbLedger(0.017, session_scope=transaction)
    guard = SpendGuard(ledger, 1000, 3000, 0.017)
    day = ["2026-09-20"]
    monkeypatch.setattr(SpendGuard, "today", staticmethod(lambda: day[0]))
    guard.reserve(100)
    day[0] = "2026-09-21"
    if actual:
        guard.record(actual)
    else:
        guard.release()
    with Session(engine) as session:
        assert session.get(SpendDay, date(2026, 9, 20)).reads == actual
        assert session.get(SpendDay, date(2026, 9, 21)) is None
        entries = session.scalars(select(SpendEntry)).all()
        assert len(entries) == bool(actual)
        if entries:
            assert entries[0].day == date(2026, 9, 20)
            assert float(entries[0].cost_usd) == pytest.approx(actual * 0.017)
    engine.dispose()
