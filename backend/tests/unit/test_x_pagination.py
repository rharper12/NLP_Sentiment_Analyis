"""Pagination targets retained posts and preserves the reason a fetch paused."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from sentiment_prep.budget import RequestBudget, current_budget
from sentiment_prep.models import CollectionProgress
from sentiment_prep.sources.spend_guard import InMemoryLedger, SpendGuard
from sentiment_prep.sources.x_search import XSearchSource


def post(identifier, text=None):
    return {
        "id": str(identifier),
        "text": text or f"Opinion number {identifier} has several useful words",
    }


@pytest.fixture
def collection():
    ledger = InMemoryLedger()
    return ledger, SpendGuard(ledger, 1000, 3000, 0.005)


def test_refills_after_exact_and_near_duplicates_across_pages(collection):
    ledger, guard = collection
    base = (
        "the coverage of this trial has been relentless and unfair and the reporting "
        "has ignored every inconvenient detail from the first day onwards"
    )
    pages = [
        {"data": [post(1), post(2, base)], "meta": {"next_token": "second"}},
        {
            "data": [post(3, post(1)["text"]), post(4, base.replace("onwards", "onward"))],
            "meta": {"next_token": "third"},
        },
        {"data": [post(5)], "meta": {"next_token": "fourth"}},
    ]
    requests, saved = [], []

    def page(request):
        requests.append(request)
        return httpx.Response(200, json=pages[len(requests) - 1])

    with httpx.Client(transport=httpx.MockTransport(page), base_url="https://x.test/2") as client:
        result = XSearchSource(guard, client).fetch(
            3,
            "iphone",
            dedupe_similarity=0.9,
            persist=lambda ds, state: saved.append(state.model_copy(deep=True)),
        )
    assert [r.id for r in result.records] == ["1", "2", "5"]
    assert result.filtered_out == {"duplicate": 1, "near_duplicate": 1}
    assert [r.url.params.get("next_token") for r in requests] == [None, "second", "third"]
    assert saved[-1].complete and saved[-1].billed_reads == 5
    assert ledger.get(guard.today()) == 5


def test_deduplication_can_be_disabled(collection):
    _, guard = collection
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "data": [post(1), post(2, post(1)["text"])],
                    "meta": {"next_token": "unused"},
                },
            )
        ),
        base_url="https://x.test/2",
    ) as client:
        result = XSearchSource(guard, client).fetch(2, "iphone")
    assert len(result.records) == 2 and result.filtered_out == {}


def test_resume_keeps_unique_target_cursor_counters_and_labels(collection):
    _, guard = collection
    calls, saved = [], []

    def page(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "data": [post(1), post(2, post(1)["text"])]
                if len(calls) == 1
                else [post(3), post(4)],
                "meta": {"next_token": "second"} if len(calls) == 1 else {},
            },
        )

    def persist(ds, state):
        saved.append((ds.model_copy(deep=True), state.model_copy(deep=True)))

    with httpx.Client(transport=httpx.MockTransport(page), base_url="https://x.test/2") as client:
        XSearchSource(guard, client).fetch(
            3,
            "iphone",
            dedupe_similarity=0.9,
            should_stop=lambda: len(calls) == 1,
            progress=CollectionProgress(
                request={"limit": 3}, billed_reads=0, committed_cost_usd=Decimal(0)
            ),
            persist=persist,
        )
        original, progress = saved[-1]
        assert len(original.records) == 1 and not progress.complete
        original.records[0].label = "positive"
        result = XSearchSource(guard, client).fetch(
            3,
            "iphone",
            dedupe_similarity=0.9,
            resume=original,
            progress=progress,
            persist=persist,
        )
    assert [r.id for r in result.records] == ["1", "3", "4"]
    assert result.records[0].label == "positive"
    assert result.filtered_out == {"duplicate": 1}
    assert calls[1].url.params["next_token"] == "second"
    assert saved[-1][1].billed_reads == 4
    assert saved[-1][1].committed_cost_usd == Decimal("0.020")


@pytest.mark.parametrize("daily", [False, True])
def test_replacement_pages_still_obey_spend_caps(daily):
    ledger = InMemoryLedger()
    guard = SpendGuard(ledger, 1000 if daily else 10, 10 if daily else 3000, 0.005)
    saved = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "data": [post(i, post(0)["text"]) for i in range(10)],
                    "meta": {"next_token": "more"},
                },
            )
        ),
        base_url="https://x.test/2",
    ) as client:
        result = XSearchSource(guard, client).fetch(
            10,
            "iphone",
            dedupe_similarity=0.9,
            persist=lambda ds, state: saved.append(state.model_copy(deep=True)),
        )
    assert len(result.records) == 1 and result.filtered_out == {"duplicate": 9}
    assert "cap" in result.truncated_reason
    assert not saved[-1].complete and saved[-1].next_token == "more"
    assert ledger.get(guard.today()) == 10


def test_empty_page_with_cursor_is_not_exhaustion(collection):
    _, guard = collection
    calls = []

    def page(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "data": [] if len(calls) == 1 else [post(1)],
                "meta": {"next_token": "next"} if len(calls) == 1 else {},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(page), base_url="https://x.test/2") as client:
        result = XSearchSource(guard, client).fetch(10, "iphone", dedupe_similarity=0.9)
    assert len(calls) == 2 and len(result.records) == 1
    assert "no more matching posts" in result.truncated_reason


def test_budget_expiring_between_reservation_and_call_is_not_rate_limit(collection, monkeypatch):
    ledger, guard = collection
    now = [0.0]
    monkeypatch.setattr("sentiment_prep.budget.time", SimpleNamespace(monotonic=lambda: now[0]))
    original_reserve = guard.reserve

    def reserve(count):
        original_reserve(count)
        now[0] = 10.0

    monkeypatch.setattr(guard, "reserve", reserve)
    saved = []
    token = current_budget.set(RequestBudget(deadline=22))
    try:
        with httpx.Client(
            transport=httpx.MockTransport(lambda r: pytest.fail("must not call X"))
        ) as client:
            result = XSearchSource(guard, client).fetch(
                10,
                "iphone",
                persist=lambda ds, state: saved.append(state.model_copy(deep=True)),
            )
    finally:
        current_budget.reset(token)
    assert result.truncated_reason == "request budget reached; resume to continue"
    assert saved[-1].retry_at == 0 and not saved[-1].complete
    assert ledger.reserved(guard.today()) == 0


def test_archive_pacing_budget_pause_is_not_rate_limit(collection, monkeypatch):
    ledger, guard = collection
    clock = SimpleNamespace(monotonic=lambda: 0.0)
    monkeypatch.setattr("sentiment_prep.budget.time", clock)
    monkeypatch.setattr(
        "sentiment_prep.sources.x_search.time",
        SimpleNamespace(monotonic=lambda: 0.0, time=lambda: 0.0),
    )
    saved, calls = [], []

    def page(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [post(1)], "meta": {"next_token": "next"}})

    token = current_budget.set(RequestBudget(deadline=16.5))
    try:
        with httpx.Client(
            transport=httpx.MockTransport(page), base_url="https://x.test/2"
        ) as client:
            result = XSearchSource(
                guard, client, start_time=datetime(2020, 1, 1, tzinfo=UTC)
            ).fetch(
                10,
                "iphone",
                persist=lambda ds, state: saved.append(state.model_copy(deep=True)),
            )
    finally:
        current_budget.reset(token)
    assert len(calls) == 1 and ledger.reserved(guard.today()) == 1
    assert result.truncated_reason == "request budget reached; resume to continue"
    assert saved[-1].retry_at == 0 and saved[-1].next_token == "next"


def test_actual_rate_limit_retains_retry_time(collection, monkeypatch):
    _, guard = collection
    monkeypatch.setattr("sentiment_prep.budget.time", SimpleNamespace(monotonic=lambda: 0.0))
    monkeypatch.setattr(
        "sentiment_prep.sources.x_search.time", SimpleNamespace(time=lambda: 1000.0)
    )
    token = current_budget.set(RequestBudget(deadline=22))
    saved = []
    try:
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    429,
                    headers={"x-rate-limit-reset": "1060"},
                )
            ),
            base_url="https://x.test/2",
        ) as client:
            result = XSearchSource(guard, client).fetch(
                10,
                "iphone",
                persist=lambda ds, state: saved.append(state.model_copy(deep=True)),
            )
    finally:
        current_budget.reset(token)
    assert result.truncated_reason == "rate limited; resume after retry time"
    assert saved[-1].retry_at == 1060.0
