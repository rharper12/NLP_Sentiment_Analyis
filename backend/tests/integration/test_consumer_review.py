"""Review, restart, protected export, and additional-candidate API contracts."""

import io
import json
from datetime import UTC, date, datetime

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from sentiment_prep.api import deps
from sentiment_prep.api.app import create_app
from sentiment_prep.config import Settings, get_settings
from sentiment_prep.eligibility import screen_candidates
from sentiment_prep.models import ConsumerPolicy, Dataset, DatasetBundle, Record
from sentiment_prep.storage.checkpoints import LocalCheckpointStore
from sentiment_prep.storage.local_repository import LocalRepository


@pytest.fixture
def review(tmp_path, monkeypatch):
    policy = ConsumerPolicy(
        start_date=date(2026, 9, 9), end_date=date(2026, 9, 10), reviewed_target=2
    )
    records = [
        Record(
            id=str(i),
            text=text,
            source_type="x",
            lang="en",
            created_at=datetime(2026, 9, 9, 12, tzinfo=UTC),
        )
        for i, text in enumerate(
            [
                "I love iPhone Duo",
                "I hate iPhone Duo",
                "Win an iPhone Duo! Follow and repost!",
                "iPhone Duo?",
                "Apple announces iPhone Duo https://example.test",
            ]
        )
    ]
    records[0].comprehend_label = "negative"
    records[0].comprehend_confidence = 0.76
    work = tmp_path / "checkpoints" / "_work"
    repo = LocalRepository(work)
    repo.save(
        DatasetBundle(
            dataset_id="consumer",
            original=Dataset(
                records=screen_candidates(records, 0.9), source_type="x", consumer_policy=policy
            ),
        )
    )
    store = LocalCheckpointStore(tmp_path / "checkpoints")
    settings = Settings(
        _env_file=None,
        api_key="offline-test-key",
        comprehend_enabled=True,
        checkpoint_dir=str(tmp_path / "checkpoints"),
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[deps.get_repository] = lambda: LocalRepository(work)
    app.dependency_overrides[deps.get_checkpoint_store] = lambda: store
    monkeypatch.setattr(
        deps,
        "get_comprehend_client",
        lambda: pytest.fail("Must not enrich eligibility or preprocessing"),
    )
    monkeypatch.setattr(deps, "get_x_source", lambda *a: pytest.fail("Review must not collect"))
    client = TestClient(app, headers={"X-API-Key": "offline-test-key"})
    return client, repo, store


def decide(client, identifier, decision, **extra):
    return client.put(
        "/dataset/consumer/eligibility",
        json={"items": [{"id": identifier, "decision": decision, **extra}]},
    )


def export(client):
    response = client.get("/dataset/consumer/reviewed.parquet")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    table = pq.read_table(io.BytesIO(response.content))
    return table.to_pylist(), json.loads(table.schema.metadata[b"sentiment_prep"])


def test_clean_after_combined_review_refreshes_labelled_snapshot_without_relabeling(review):
    client, repo, store = review
    assert decide(client, "0", "include", label="positive").status_code == 200
    before = repo.get("consumer").original.records[0]
    result = client.post("/dataset/consumer/preprocess", json={"steps": ["lowercase"]})
    assert result.status_code == 200
    assert not any("checkpoint" in warning for warning in result.json()["warnings"])
    saved = repo.get("consumer")
    assert saved.checkpoint_status["labelled:csv"].status == "current"
    assert saved.original.records[0] == before
    content = store.read("consumer", "labelled", "csv")
    assert content and b"i love iphone duo" in content and b"positive" in content


def test_reviewed_export_confirms_unchanged_choices_preserves_scores_and_rebuilds(review):
    client, repo, store = review
    url = "/dataset/consumer"
    assert client.post(url + "/preprocess", json={"steps": ["lowercase"]}).status_code == 200
    assert repo.get("consumer").report is not None
    assert export(client)[0] == []
    assert decide(client, "0", "include").status_code == 200
    assert decide(client, "1", "include").status_code == 200
    assert decide(client, "2", "exclude", reason="giveaway_or_promotion").status_code == 200
    for identifier, label in [("0", "positive"), ("1", "negative")]:
        for _ in range(2):
            assert (
                client.post(
                    url + "/labels/manual", json={"items": [{"id": identifier, "label": label}]}
                ).status_code
                == 200
            )
    rows, metadata = export(client)
    assert [r["id"] for r in rows] == ["0", "1"]
    assert rows[0]["comprehend_label"] == "negative" and rows[0]["comprehend_confidence"] == 0.76
    assert rows[1]["comprehend_confidence"] is None and rows[1]["label_confidence"] is None
    assert all(r["sentiment_reviewed"] and r["eligibility_reviewed"] for r in rows)
    assert metadata["status"] == "complete" and metadata["counts"]["reviewed_final"] == 2
    assert metadata["policy"]["timezone"] == "America/Chicago"
    saved = repo.get("consumer")
    assert saved.processed is None and saved.report is None and saved.analysis.signature == ""
    assert saved.checkpoint_status["processed:csv"].status == "stale"
    assert client.post(url + "/checkpoints/processed/parquet").status_code == 409
    assert decide(client, "1", "exclude", reason="news_or_article").status_code == 400
    assert (
        decide(
            client,
            "1",
            "exclude",
            reason="news_or_article",
            note="I initially missed the quoted headline",
        ).status_code
        == 200
    )
    rows, metadata = export(client)
    assert [r["id"] for r in rows] == ["0"]
    assert metadata["status"] == "partial" and metadata["counts"]["shortfall"] == 1
    page = client.get(url + "/eligibility?status=exclude").json()
    assert [r["id"] for r in page["items"]] == ["1", "2"]
    assert len(page["items"][0]["eligibility_history"]) == 2
    assert decide(client, "1", "pending").status_code == 200
    assert len(export(client)[0]) == 1
    assert len(repo.get("consumer").original.records) == 5
    assert store.read("consumer", "collected", "csv") is None


def test_excluded_and_pending_rows_cannot_be_sentiment_reviewed(review):
    client, _, _ = review
    for identifier in ("2", "3"):
        response = client.post(
            "/dataset/consumer/labels/manual",
            json={"items": [{"id": identifier, "label": "neutral"}]},
        )
        assert response.status_code == 400
    assert client.get("/dataset/consumer/eligibility?status=sentiment").json()["total"] == 0


def test_local_reopen_preserves_review_identity_and_existing_bundle(review):
    client, repo, _ = review
    assert decide(client, "0", "include").status_code == 200
    assert decide(client, "2", "exclude", reason="giveaway_or_promotion").status_code == 200
    before = repo.get("consumer").model_dump(mode="json")
    response = client.post("/local-datasets/consumer/restore")
    assert response.status_code == 200
    assert response.json()["dataset_id"] == "consumer"
    assert response.json()["consumer_counts"]["human_exclusions"] == 1
    assert repo.get("consumer").model_dump(mode="json") == before


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/eligibility", None),
        ("PUT", "/eligibility", {"items": [{"id": "0", "decision": "include"}]}),
        ("GET", "/reviewed.parquet", None),
        ("GET", "/export.csv", None),
        ("GET", "/export.parquet", None),
        ("POST", "/candidates", {"candidate_target": 100, "confirm_cost": True}),
    ],
)
def test_all_review_export_and_additional_routes_require_authorization(review, method, path, body):
    client, _, _ = review
    response = client.request(
        method, "/dataset/consumer" + path, json=body, headers={"X-API-Key": "wrong"}
    )
    assert response.status_code == 401


def test_additional_collection_requires_explicit_cost_confirmation(review):
    client, _, _ = review
    response = client.post("/dataset/consumer/candidates", json={"candidate_target": 100})
    assert response.status_code == 400
    assert "Confirm the cost" in response.text
