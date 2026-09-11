import httpx
import pytest

from sentiment_prep.errors import ValidationError
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


def x_client(pages: list[httpx.Response]) -> httpx.Client:  # noqa: D103
    calls = iter(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        return next(calls)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.x.test/2")


def guard(per_fetch=1000, per_day=3000) -> SpendGuard:
    return SpendGuard(InMemoryLedger(), per_fetch, per_day, 0.005)


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


def test_spend_guard_daily_cap():
    g = guard(per_day=50)
    g.reserve(50)
    g.record(50)
    with pytest.raises(SpendCapReachedError):
        g.reserve(1)


def test_db_ledger_is_append_only():
    ledger = DbLedger(0.005, query="test")
    day = "2030-01-01"
    assert ledger.get(day) == 0
    ledger.add(day, 40)
    ledger.add(day, 10)
    assert DbLedger(0.005).get(day) == 50


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
