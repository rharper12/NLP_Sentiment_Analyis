"""Exact retained-target scenarios, using local storage and deterministic provider pages."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from sentiment_prep.api import collection as collection_service
from sentiment_prep.api import deps
from sentiment_prep.api.schemas import LoadRequest
from sentiment_prep.budget import RequestBudget, current_budget
from sentiment_prep.config import Settings
from sentiment_prep.models import CollectionProgress, Dataset, DatasetBundle, Record
from sentiment_prep.sources.spend_guard import InMemoryLedger, SpendGuard
from sentiment_prep.sources.x_search import XSearchSource
from sentiment_prep.storage.checkpoints import LocalCheckpointStore
from sentiment_prep.storage.repository import InMemoryRepository


def post(number, text=None):
    return {
        "id": str(number),
        "text": text or f"Opinion {number} has several useful words",
        "lang": "en",
    }


@pytest.fixture
def collection(tmp_path, monkeypatch):
    repo = InMemoryRepository()
    ledger = InMemoryLedger()
    settings = Settings(_env_file=None, dedupe_enabled=True)
    body = LoadRequest(source="x", query="verification", limit=500, request_id="verify-collection")
    harness = SimpleNamespace(repo=repo, ledger=ledger, requests=[], per_fetch=1000, per_day=3000)
    harness.reply = lambda request: pytest.fail("Unexpected provider call")
    store = LocalCheckpointStore(tmp_path)

    def handler(request):
        harness.requests.append(request)
        return harness.reply(request)

    def seed(count, *, complete=False, age=1, retry_at=0, stop_reason=None):
        now = datetime.now(UTC)
        repo.save(
            DatasetBundle(
                dataset_id="x-verify-collection",
                original=Dataset(
                    records=[Record(**post(i), source_type="x") for i in range(count)],
                    source_type="x",
                    window_start=now - timedelta(days=age),
                    window_end=now - timedelta(minutes=1),
                    truncated_reason=stop_reason,
                ),
                collection=CollectionProgress(
                    request=body.model_dump(mode="json", exclude={"request_id"}),
                    next_token="saved-cursor",
                    reads=count,
                    billed_reads=count,
                    committed_cost_usd=Decimal(count) * Decimal("0.005"),
                    complete=complete,
                    search_mode="recent",
                    retry_at=retry_at,
                    stop_reason=stop_reason,
                ),
            )
        )
        day = SpendGuard.today()
        assert ledger.reserve(day, count, 3000)
        ledger.settle(day, count, count, "verification")

    harness.seed = seed
    harness.load = lambda: collection_service.collect_x(body, repo, store, settings)
    harness.bundle = lambda: repo.get("x-verify-collection")
    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://x.test/2"
    ) as client:
        monkeypatch.setattr(
            deps,
            "get_x_source",
            lambda *a: XSearchSource(
                SpendGuard(ledger, harness.per_fetch, harness.per_day, 0.005),
                client,
            ),
        )
        yield harness


def test_exact_500_raw_to_476_retained_then_500_target(collection):
    c = collection
    unique = [post(i) for i in range(472)]
    bases = [" ".join(f"group{i}word{j}" for j in range(25)) for i in range(4)]
    unique += [post(472 + i, text) for i, text in enumerate(bases)]
    raw = unique + [post(476 + i, unique[i]["text"]) for i in range(20)]
    raw += [post(496 + i, text.replace("word24", "changed")) for i, text in enumerate(bases)]
    assert len(raw) == 500

    def page(request):
        number = len(c.requests) - 1
        if number < 5:
            return httpx.Response(
                200,
                json={
                    "data": raw[number * 100 : (number + 1) * 100],
                    "meta": {"next_token": f"page-{number + 1}"},
                },
            )
        saved = c.bundle()
        assert len(saved.original.records) == 476
        assert saved.original.filtered_out == {"duplicate": 20, "near_duplicate": 4}
        assert not saved.collection.complete
        assert saved.collection.billed_reads == 500
        assert request.url.params["next_token"] == "page-5"
        assert request.url.params["max_results"] == "24"
        return httpx.Response(
            200,
            json={
                "data": [post(i) for i in range(500, 524)],
                "meta": {"next_token": "unused-page"},
            },
        )

    c.reply = page
    result = c.load()
    assert result.record_count == 500 and not result.partial and result.truncated_reason is None
    assert c.bundle().collection.complete and c.bundle().collection.stop_reason is None
    assert result.billed_reads == 524 and result.committed_cost_usd == pytest.approx(2.62)
    assert c.ledger.get(SpendGuard.today()) == 524
    c.load()
    assert len(c.requests) == 6


def test_exact_499_request_budget_pause_preserves_resume(collection, monkeypatch):
    c = collection
    c.seed(499)
    monkeypatch.setattr("sentiment_prep.budget.time", SimpleNamespace(monotonic=lambda: 10.0))
    token = current_budget.set(RequestBudget(deadline=22))
    try:
        result = c.load()
    finally:
        current_budget.reset(token)
    assert result.record_count == 499 and result.partial
    assert result.truncated_reason == "request budget reached; resume to continue"
    assert result.retry_at is None and result.resume_request_id == "verify-collection"
    assert not c.bundle().collection.complete and c.bundle().collection.next_token == "saved-cursor"
    assert not c.requests and result.billed_reads == 499


@pytest.mark.parametrize("with_reset", [True, False])
def test_real_429_keeps_rows_cursor_and_known_retry_schedule(collection, monkeypatch, with_reset):
    c = collection
    c.seed(499)
    monkeypatch.setattr("sentiment_prep.budget.time", SimpleNamespace(monotonic=lambda: 0.0))
    monkeypatch.setattr(
        "sentiment_prep.sources.x_search.time", SimpleNamespace(time=lambda: 1000.0)
    )
    c.reply = lambda r: httpx.Response(
        429, headers={"x-rate-limit-reset": "1060"} if with_reset else {}
    )
    token = current_budget.set(RequestBudget(deadline=17))
    try:
        result = c.load()
    finally:
        current_budget.reset(token)
    assert result.record_count == 499 and result.partial
    assert result.truncated_reason == "rate limited; resume after retry time"
    # With no provider reset header, the application's known backoff is two seconds.
    assert result.retry_at == (1060 if with_reset else 1002)
    assert c.bundle().collection.next_token == "saved-cursor" and result.billed_reads == 499
    assert c.ledger.reserved(SpendGuard.today()) == 499


def test_resume_old_premature_job_ignores_duplicate_rows_and_bills_them(collection):
    c = collection
    c.seed(476, complete=True)

    def page(request):
        if len(c.requests) == 1:
            assert request.url.params["next_token"] == "saved-cursor"
            return httpx.Response(
                200,
                json={
                    "data": [post(0), post(1000, post(1)["text"])],
                    "meta": {"next_token": "after-duplicates"},
                },
            )
        assert c.bundle().collection.billed_reads == 478
        assert len(c.bundle().original.records) == 476
        assert request.url.params["next_token"] == "after-duplicates"
        return httpx.Response(200, json={"data": [post(i) for i in range(476, 500)], "meta": {}})

    c.reply = page
    result = c.load()
    assert result.dataset_id == "x-verify-collection" and not result.partial
    assert result.record_count == 500 and result.filtered_out == {"duplicate": 1}
    assert result.billed_reads == 502 and result.committed_cost_usd == pytest.approx(2.51)


def test_exhaustion_below_target_is_terminal_and_has_a_reason(collection):
    c = collection
    c.seed(499)
    c.reply = lambda r: httpx.Response(200, json={"data": [post(0)], "meta": {}})
    result = c.load()
    assert result.record_count == 499 and not result.partial
    assert result.truncated_reason == "no more matching posts in the selected window"
    assert c.bundle().collection.complete and c.bundle().collection.next_token is None
    c.load()
    assert len(c.requests) == 1


@pytest.mark.parametrize("daily", [True, False])
def test_cap_at_499_makes_no_call_and_preserves_progress(collection, daily):
    c = collection
    c.seed(499)
    if daily:
        c.per_day = 499
    else:
        c.per_fetch = 499
    result = c.load()
    assert result.record_count == 499 and result.partial and not c.requests
    assert result.truncated_reason.startswith("daily cap" if daily else "per-fetch cap")
    assert result.retry_at is None and result.billed_reads == 499
    assert c.bundle().collection.next_token == "saved-cursor"


@pytest.mark.parametrize("age", [1, 8])
def test_already_satisfied_resume_ignores_expiry_and_old_retry_time(collection, age):
    c = collection
    c.seed(500, age=age, retry_at=4_000_000_000, stop_reason="rate limited")
    result = c.load()
    assert not c.requests and result.record_count == 500 and not result.partial
    assert result.truncated_reason is None and result.retry_at is None
    assert c.bundle().collection.complete and result.billed_reads == 500


def test_mixed_invalid_duplicate_and_valid_page_never_claims_target(collection):
    c = collection
    c.seed(499)
    c.reply = lambda r: httpx.Response(
        200,
        json={
            "data": [post(0), post(600, post(1)["text"]), post(601), {"id": "bad", "text": None}],
            "meta": {"next_token": "unsafe-cursor"},
        },
    )
    result = c.load()
    # The existing boundary rejects a malformed page atomically, preserving the prior cursor.
    assert result.record_count == 499 and result.partial
    assert "malformed" in result.truncated_reason and result.retry_at is None
    assert c.bundle().collection.next_token == "saved-cursor"
    assert result.billed_reads == 499 and result.committed_cost_usd == pytest.approx(2.495)
    assert c.bundle().collection.reads == 509  # Ambiguous page retains its full reservation.
    assert c.ledger.reserved(SpendGuard.today()) == 509


def test_other_collection_filters_run_before_target_check(collection):
    c = collection
    c.seed(499)

    def page(request):
        if len(c.requests) == 1:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {**post(500), "lang": "fr"},
                        post(501, "too short"),
                        post(502, post(0)["text"]),
                    ],
                    "meta": {"next_token": "qualifying"},
                },
            )
        assert len(c.bundle().original.records) == 499
        assert request.url.params["next_token"] == "qualifying"
        return httpx.Response(200, json={"data": [post(503)], "meta": {}})

    c.reply = page
    result = c.load()
    assert result.record_count == 500 and result.billed_reads == 503 and not result.partial
    assert result.filtered_out == {"not_english": 1, "no_content_after_cleaning": 1, "duplicate": 1}


def test_boundary_near_duplicate_does_not_satisfy_target(collection):
    c = collection
    c.seed(499)
    base = "alpha bravo charlie delta echo foxtrot golf hotel india"
    with c.repo.edit("x-verify-collection") as edit:
        bundle = edit.bundle
        bundle.original.records[0].text = base
        edit.save(bundle)

    def page(request):
        if len(c.requests) == 1:
            return httpx.Response(
                200,
                json={"data": [post(500, f"{base} zulu")], "meta": {"next_token": "qualifying"}},
            )
        assert len(c.bundle().original.records) == 499
        assert not c.bundle().collection.complete
        return httpx.Response(200, json={"data": [post(501)], "meta": {}})

    c.reply = page
    result = c.load()
    assert result.record_count == 500 and not result.partial
    assert result.filtered_out == {"near_duplicate": 1} and result.billed_reads == 501


def test_unrecoverable_query_rejection_is_terminal_with_saved_progress(collection):
    c = collection
    c.seed(499)
    c.reply = lambda r: httpx.Response(400)
    result = c.load()
    assert result.record_count == 499 and not result.partial
    assert (
        "HTTP 400" in result.truncated_reason and "resume to retry" not in result.truncated_reason
    )
    assert result.retry_at is None and result.billed_reads == 499
    assert c.bundle().collection.complete and c.bundle().collection.next_token == "saved-cursor"
    c.load()
    assert len(c.requests) == 1
