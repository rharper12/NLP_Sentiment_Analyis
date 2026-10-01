"""Bounded day slices, resume accounting, and local date validation with mocked X."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError as ModelValidationError

from sentiment_prep.api import collection as collection_service
from sentiment_prep.api import deps
from sentiment_prep.api.app import create_app
from sentiment_prep.api.schemas import LoadRequest
from sentiment_prep.config import Settings, get_settings
from sentiment_prep.errors import ValidationError
from sentiment_prep.models import ConsumerPolicy
from sentiment_prep.sources.spend_guard import InMemoryLedger, SpendGuard
from sentiment_prep.sources.x_search import XSearchSource
from sentiment_prep.storage.checkpoints import LocalCheckpointStore
from sentiment_prep.storage.repository import InMemoryRepository


def request_body(**changes):
    return LoadRequest.model_validate(
        {
            "source": "x",
            "request_id": "consumer-test",
            "preset": "consumer_reactions",
            "start_date": "2026-09-09",
            "end_date": "2026-09-10",
            "timezone": "America/Chicago",
            "limit": 20,
            **changes,
        }
    )


def test_dates_include_entire_final_day_and_convert_timezone():
    body = request_body()
    assert body.start_time == datetime(2026, 9, 9, 5, tzinfo=UTC)
    assert body.end_time == datetime(2026, 9, 11, 5, tzinfo=UTC)
    policy = ConsumerPolicy(start_date=date(2026, 3, 8), end_date=date(2026, 3, 8))
    assert policy.bounds()[1] - policy.bounds()[0] == timedelta(hours=23)
    policy = ConsumerPolicy(start_date=date(2025, 11, 2), end_date=date(2025, 11, 2))
    assert policy.bounds()[1] - policy.bounds()[0] == timedelta(hours=25)


@pytest.mark.parametrize(
    "changes",
    [
        {"end_date": "2026-09-08"},
        {"timezone": "invalid/zone"},
        {"start_date": None},
        {"start_time": "2026-09-09T00:00:00Z"},
        {"end_date": "2099-09-10"},
        {"end_date": "9999-12-31"},
        {"end_date": "2026-09-10", "start_date": "2026-08-09"},
    ],
)
def test_invalid_ranges_fail_before_spending(changes):
    with pytest.raises(ModelValidationError):
        request_body(**changes)


def test_general_calendar_search_preserves_long_archive_ranges():
    body = LoadRequest.model_validate(
        {"source": "x", "start_date": "2025-01-01", "end_date": "2025-12-31"}
    )
    assert body.end_time == datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def collection(tmp_path, monkeypatch):
    repo, ledger = InMemoryRepository(), InMemoryLedger()
    settings = Settings(_env_file=None)
    harness = SimpleNamespace(repo=repo, ledger=ledger, requests=[], body=request_body(), cap=1000)
    harness.reply = lambda request: pytest.fail("Unexpected paid request")
    checkpoints = LocalCheckpointStore(tmp_path)
    harness.settings, harness.checkpoints = settings, checkpoints
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[deps.get_repository] = lambda: repo
    app.dependency_overrides[deps.get_checkpoint_store] = lambda: checkpoints
    harness.client = TestClient(app)

    def handler(request):
        harness.requests.append(request)
        return harness.reply(request)

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://x.test/2"
    ) as client:
        monkeypatch.setattr(
            deps,
            "get_x_source",
            lambda query, start, end: XSearchSource(
                SpendGuard(ledger, harness.cap, 3000, 0.005), client, start_time=start, end_time=end
            ),
        )
        monkeypatch.setattr("sentiment_prep.sources.x_search.time.sleep", lambda seconds: None)
        harness.load = lambda: collection_service.collect_x(
            harness.body, repo, checkpoints, settings
        )
        harness.bundle = lambda: repo.get("x-consumer-test")
        yield harness


def post(identifier, timestamp, **changes):
    return {
        "id": identifier,
        "text": "I love iPhone Duo",
        "lang": "en",
        "created_at": timestamp,
        **changes,
    }


def test_calendar_slices_preserve_originals_metadata_and_costs(collection):
    c = collection
    pages = [
        [
            post("before", "2026-09-09T04:59:59Z"),
            post("start", "2026-09-09T05:00:00Z", author_id="0001"),
            post("edge", "2026-09-10T05:00:00Z"),
            post("missing", None),
        ],
        [
            post(
                "end-minus",
                "2026-09-11T04:59:59.999Z",
                note_tweet={"text": "I hate iPhone Duo. " + "complete " * 100},
                conversation_id="0002",
                referenced_tweets=[{"type": "quoted", "id": "0003"}],
            ),
            post("end", "2026-09-11T05:00:00Z"),
        ],
    ]
    c.reply = lambda r: httpx.Response(200, json={"data": pages[len(c.requests) - 1], "meta": {}})
    result = c.load()
    assert [r.id for r in c.bundle().original.records] == ["start", "end-minus"]
    assert result.window_start == c.body.start_time and result.window_end == c.body.end_time
    assert result.filtered_out == {"out_of_window": 3, "missing_timestamp": 1}
    assert result.consumer_counts.retrieved == result.billed_reads == 6
    assert result.consumer_counts.unique_records == 6
    assert result.consumer_counts.screened_candidates == 2
    assert result.consumer_counts.reviewed_final == 0
    assert [d.candidates for d in result.consumer_counts.days] == [1, 1]
    assert c.bundle().original.records[0].author_id == "0001"
    assert c.bundle().original.records[1].text.endswith("complete ")
    assert c.bundle().original.records[1].references[0].id == "0003"
    assert c.requests[0].url.params["end_time"] == c.requests[1].url.params["start_time"]
    assert all(
        r.url.path.endswith("/search/all") and "expansions" not in r.url.params for r in c.requests
    )
    c.load()
    assert len(c.requests) == 2  # exhausted is terminal


def test_additional_target_preserves_day_cursors_decisions_seen_ids_and_spend(collection):
    c = collection

    def reply(request):
        day = request.url.params["start_time"][8:10]
        cursor = request.url.params.get("next_token")
        data = [post(f"{day}-{i}", f"2026-09-{day}T12:00:00Z") for i in range(10)]
        if cursor:
            data += [post(f"{day}-more", f"2026-09-{day}T11:00:00Z")]
        return httpx.Response(
            200, json={"data": data, "meta": {} if cursor else {"next_token": f"{day}-cursor"}}
        )

    c.reply = reply
    first = c.load()
    assert first.record_count == 20 and first.can_collect_more
    assert first.consumer_counts.retrieved == 20
    with c.repo.edit("x-consumer-test") as edit:
        saved = edit.bundle
        saved.original.records[0].eligibility = "exclude"
        saved.original.records[0].eligibility_reviewed = True
        edit.save(saved)
    response = c.client.post(
        "/dataset/x-consumer-test/candidates", json={"candidate_target": 40, "confirm_cost": True}
    )
    assert response.status_code == 200
    result = collection_service.summarize(c.bundle())
    assert result.record_count == 22 and result.billed_reads == 42
    assert result.consumer_counts.unique_records == 22
    assert result.consumer_counts.human_exclusions == 1
    assert result.committed_cost_usd == pytest.approx(0.21)
    assert [r.url.params.get("next_token") for r in c.requests] == [
        None,
        None,
        "09-cursor",
        "10-cursor",
    ]
    assert not result.can_collect_more and not result.partial
    assert (
        c.client.post(
            "/dataset/x-consumer-test/candidates",
            json={"candidate_target": 40, "confirm_cost": True},
        ).status_code
        == 200
    )
    assert len(c.requests) == 4  # Retrying the same target cannot double the authorized quota.
    assert (
        c.client.post(
            "/dataset/x-consumer-test/candidates",
            json={"candidate_target": 30, "confirm_cost": True},
        ).status_code
        == 400
    )


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404])
def test_terminal_errors_never_retry_or_fallback(collection, status):
    c = collection
    c.reply = lambda r: httpx.Response(status)
    result = c.load()
    assert not result.partial and c.bundle().collection.terminal_error
    assert result.retry_at is None and result.billed_reads == 0
    if status == 403:
        assert "full-archive access" in result.truncated_reason
    c.load()
    assert len(c.requests) == 1 and c.requests[0].url.path.endswith("/search/all")


def test_changed_criteria_cannot_reuse_saved_progress(collection):
    c = collection
    c.reply = lambda r: httpx.Response(200, json={"data": [], "meta": {}})
    c.load()
    for change in [{"per_author_limit": 3}, {"query": "new query"}, {"end_date": "2026-09-09"}]:
        c.body = request_body(**change)
        with pytest.raises(ValidationError, match="different collection"):
            c.load()
    assert len(c.requests) == 2


def test_request_pause_preserves_cursor_and_does_not_invent_throttle(collection, monkeypatch):
    c = collection
    c.reply = lambda r: httpx.Response(
        200,
        json={
            "data": [post("one", "2026-09-09T12:00:00Z")],
            "meta": {"next_token": "saved-cursor"},
        },
    )
    monkeypatch.setattr("sentiment_prep.sources.x_search.can_start", lambda: not c.requests)
    result = c.load()
    assert result.partial and result.retry_at is None
    assert "request budget" in result.truncated_reason
    assert result.billed_reads == 1 and c.bundle().collection.slices[0].next_token == "saved-cursor"
    assert [d.candidates for d in result.consumer_counts.days] == [1, 0]
    monkeypatch.setattr("sentiment_prep.sources.x_search.can_start", lambda: True)
    c.reply = lambda r: httpx.Response(200, json={"data": [], "meta": {}})
    result = c.load()
    assert c.requests[1].url.params["next_token"] == "saved-cursor"
    assert result.billed_reads == 1 and not result.partial
    assert result.consumer_counts.shortfall == 500


def test_spend_cap_survives_additional_candidate_requests(collection):
    c = collection
    c.cap = 10
    c.reply = lambda r: httpx.Response(
        200,
        json={"data": [post(str(i), "2026-09-09T12:00:00Z") for i in range(10)], "meta": {}},
    )
    result = c.load()
    assert result.partial and result.retry_at is None and result.billed_reads == 10
    assert "cap" in result.truncated_reason
    response = c.client.post(
        "/dataset/x-consumer-test/candidates", json={"candidate_target": 40, "confirm_cost": True}
    )
    assert response.status_code == 200 and len(c.requests) == 1
    assert c.bundle().collection.reads == 10


def test_reviewed_target_blocks_more_spend_and_correction_reopens_collection(collection):
    c = collection
    c.body = request_body(reviewed_target=1)

    def reply(request):
        day = request.url.params["start_time"][8:10]
        more = request.url.params.get("next_token")
        return httpx.Response(
            200,
            json={
                "data": [
                    post(f"{day}-{bool(more)}-{i}", f"2026-09-{day}T12:00:00Z") for i in range(10)
                ],
                "meta": {} if more else {"next_token": "more"},
            },
        )

    c.reply = reply
    c.load()
    identifier = c.bundle().original.records[0].id
    base = "/dataset/x-consumer-test"
    assert (
        c.client.put(
            base + "/eligibility", json={"items": [{"id": identifier, "decision": "include"}]}
        ).status_code
        == 200
    )
    assert (
        c.client.post(
            base + "/labels/manual", json={"items": [{"id": identifier, "label": "positive"}]}
        ).status_code
        == 200
    )
    denied = c.client.post(
        base + "/candidates", json={"candidate_target": 40, "confirm_cost": True}
    )
    assert denied.status_code == 400 and "reviewed target has been reached" in denied.text
    assert len(c.requests) == 2 and c.bundle().collection.billed_reads == 20
    assert c.bundle().collection.candidate_target is None
    assert (
        c.client.put(
            base + "/eligibility", json={"items": [{"id": identifier, "decision": "pending"}]}
        ).status_code
        == 200
    )
    allowed = c.client.post(
        base + "/candidates", json={"candidate_target": 40, "confirm_cost": True}
    )
    assert allowed.status_code == 200 and len(c.requests) == 4
    assert c.bundle().collection.billed_reads == 40
    assert c.bundle().original.records[0].eligibility == "pending"


def test_resuming_original_load_cannot_spend_after_review_reaches_target(collection, monkeypatch):
    c = collection
    c.body = request_body(reviewed_target=1)
    c.reply = lambda r: httpx.Response(
        200,
        json={"data": [post("one", "2026-09-09T12:00:00Z")], "meta": {"next_token": "more"}},
    )
    monkeypatch.setattr("sentiment_prep.sources.x_search.can_start", lambda: not c.requests)
    assert c.load().partial
    with c.repo.edit("x-consumer-test") as edit:
        saved = edit.bundle
        item = saved.original.records[0]
        item.eligibility, item.eligibility_reviewed = "include", True
        item.label, item.sentiment_reviewed = "positive", True
        edit.save(saved)
    monkeypatch.setattr("sentiment_prep.sources.x_search.can_start", lambda: True)
    assert c.load().consumer_counts.shortfall == 0
    assert len(c.requests) == 1 and c.bundle().collection.billed_reads == 1


def test_genuine_throttle_survives_resumption_without_more_requests(collection):
    c = collection
    c.reply = lambda r: httpx.Response(429, headers={"x-rate-limit-reset": "4102444800"})
    result = c.load()
    assert result.partial and result.retry_at is not None and result.billed_reads == 0
    requests = len(c.requests)
    resumed = c.load()
    assert len(c.requests) == requests and resumed.retry_at == result.retry_at


def test_cancellation_preserves_candidates_and_resumes_same_daily_cursor(collection):
    c = collection
    c.reply = lambda r: httpx.Response(
        200,
        json={"data": [post("one", "2026-09-09T12:00:00Z")], "meta": {"next_token": "next"}},
    )
    result = collection_service.collect_x(
        c.body,
        c.repo,
        c.checkpoints,
        c.settings,
        lambda: bool(c.requests),
    )
    assert result.partial and "cancelled" in result.truncated_reason and result.record_count == 1
    c.reply = lambda r: httpx.Response(200, json={"data": [], "meta": {}})
    c.load()
    assert c.requests[1].url.params["next_token"] == "next"
