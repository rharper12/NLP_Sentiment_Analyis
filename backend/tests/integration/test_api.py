"""Routes via TestClient with every external dependency replaced."""

import httpx
import pytest
from fastapi.testclient import TestClient

from sentiment_prep.api import deps
from sentiment_prep.api.app import create_app
from sentiment_prep.sources.huggingface import HuggingFaceSource
from sentiment_prep.storage.repository import InMemoryRepository
from tests.conftest import FakeBedrock, FakeComprehend


def fake_hf() -> HuggingFaceSource:
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        length = int(request.url.params["length"])
        rows = [
            {
                "row_idx": offset + i,
                "row": {"text": f"I did not love item {offset + i} at all!", "label": 0},
            }
            for i in range(length)
        ]
        return httpx.Response(200, json={"rows": rows})

    return HuggingFaceSource(
        "d",
        "c",
        "s",
        "text",
        "label",
        httpx.Client(transport=httpx.MockTransport(handler), base_url="https://hf.test"),
    )


@pytest.fixture
def client(monkeypatch):
    repo = InMemoryRepository()
    monkeypatch.setattr(deps, "get_repository", lambda: repo)
    monkeypatch.setattr(deps, "get_hf_source", fake_hf)
    monkeypatch.setattr(deps, "get_comprehend_client", lambda: FakeComprehend())
    monkeypatch.setattr(deps, "get_bedrock_client", lambda: FakeBedrock())
    monkeypatch.setattr(deps, "get_comprehend_rate", lambda: None)  # offline: no price known
    app = create_app()
    app.dependency_overrides[deps.get_repository] = lambda: repo
    return TestClient(app)


def test_health_and_request_id_echo(client):
    r = client.get("/health", headers={"X-Request-Id": "req-1"})
    assert r.status_code == 200 and r.headers["X-Request-Id"] == "req-1"
    body = r.json()
    assert body["diagnostics"] is True and body["x_configured"] is False
    assert body["database"] == "sqlite" and body["checkpoints"] == "local"
    assert "X-Request-Id" in client.get("/health").headers


def test_health_hides_operator_fields_without_diagnostics(client, monkeypatch):
    from sentiment_prep.config import get_settings

    monkeypatch.setenv("DIAGNOSTICS", "false")
    get_settings.cache_clear()
    try:
        body = client.get("/health").json()
        assert body["diagnostics"] is False
        hidden = (
            "runtime",
            "database",
            "database_ephemeral",
            "checkpoints",
            "comprehend_enabled",
            "bedrock_enabled",
        )
        assert all(body[k] is None for k in hidden)
    finally:
        monkeypatch.delenv("DIAGNOSTICS")
        get_settings.cache_clear()


def test_steps_catalogue(client):
    r = client.get("/steps")
    assert r.status_code == 200 and {s["name"] for s in r.json()} >= {"tokenize", "stopwords"}


def test_load_preprocess_export_flow(client):
    loaded = client.post("/dataset/load", json={"source": "huggingface", "limit": 520}).json()
    assert loaded["record_count"] == 520 and loaded["warnings"] == []
    dataset_id = loaded["dataset_id"]

    run = client.post(
        f"/dataset/{dataset_id}/preprocess", json={"steps": ["lowercase", "tokenize", "stopwords"]}
    )
    assert run.status_code == 200
    body = run.json()
    assert body["applied_steps"] == ["lowercase", "tokenize", "stopwords"]
    assert body["report"]["sentiment"]["agreement"] is not None
    assert body["report"]["explanation"] == "Vocabulary shrank; negations kept."
    assert body["metrics_after"]["vocab_size"] <= body["metrics_before"]["vocab_size"]

    page = client.get(
        f"/dataset/{dataset_id}/records", params={"limit": 5, "search": "item 3"}
    ).json()
    assert page["items"][0]["processed"]["tokens"] is not None

    assert (
        client.get(f"/dataset/{dataset_id}/export.csv")
        .headers["content-type"]
        .startswith("text/csv")
    )
    assert client.get(f"/dataset/{dataset_id}/export.xlsx").status_code == 200
    assert "Measured impact" in client.get(f"/dataset/{dataset_id}/report.md").text


def test_short_dataset_warns(client):
    loaded = client.post("/dataset/load", json={"source": "huggingface", "limit": 40}).json()
    assert "at least 500" in loaded["warnings"][0]


def test_unknown_step_is_400_with_request_id(client):
    dataset_id = client.post("/dataset/load", json={"source": "huggingface", "limit": 10}).json()[
        "dataset_id"
    ]
    r = client.post(f"/dataset/{dataset_id}/preprocess", json={"steps": ["bogus"]})
    assert r.status_code == 400 and r.json()["request_id"]


def test_missing_dataset_is_404(client):
    assert client.get("/dataset/nope").status_code == 404


def test_upload_csv(client):
    files = {"file": ("d.csv", b"text,label\nhello world today,pos\n", "text/csv")}
    r = client.post("/dataset/upload", files=files)
    assert r.status_code == 200 and r.json()["record_count"] == 1


def test_label_flow(client, monkeypatch):
    dataset_id = client.post("/dataset/load", json={"source": "huggingface", "limit": 12}).json()[
        "dataset_id"
    ]
    est = client.get(f"/dataset/{dataset_id}/labels/estimate").json()
    assert est["records_to_send"] == 12 and est["billable_units"] >= 36
    assert est["price_status"] == "unavailable" and est["estimated_cost_usd"] is None

    refused = client.post(f"/dataset/{dataset_id}/labels/comprehend", json={"max_records": 5})
    assert refused.status_code == 400

    first = client.post(
        f"/dataset/{dataset_id}/labels/comprehend", json={"max_records": 5, "confirm_cost": True}
    ).json()
    assert first["labelled_in_call"] == 5 and first["remaining"] == 7 and not first["done"]
    second = client.post(
        f"/dataset/{dataset_id}/labels/comprehend", json={"max_records": 50, "confirm_cost": True}
    ).json()
    assert second["done"] and second["labelled_total"] == 12

    review = client.put(
        f"/dataset/{dataset_id}/labels/review",
        json={"mode": "sample", "size": 25, "unit": "percent"},
    ).json()
    assert review["review_sample_size"] == 3
    page = client.get(f"/dataset/{dataset_id}/labels/review", params={"limit": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    rid = page["items"][0]["id"]
    done = client.post(
        f"/dataset/{dataset_id}/labels/manual", json={"items": [{"id": rid, "label": "positive"}]}
    ).json()
    assert done["reviewed"] == 1 and done["by_source"]["manual"] == 1

    checkpoints = client.get(f"/dataset/{dataset_id}/checkpoints").json()
    assert checkpoints["location"] == "local"
    assert {c["stage"] for c in checkpoints["items"]} >= {"collected", "labelled"}
    converted = client.post(f"/dataset/{dataset_id}/checkpoints/labelled/parquet").json()
    assert converted["format"] == "parquet"
    assert client.get(f"/dataset/{dataset_id}/export.parquet").status_code == 200
    assert "## Labels" in client.get(f"/dataset/{dataset_id}/report.md").text


def test_x_without_token_is_503(client, monkeypatch):
    from sentiment_prep.config import get_settings

    get_settings.cache_clear()
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    r = client.post("/dataset/load", json={"source": "x", "limit": 10, "query": "test"})
    assert r.status_code == 503


def test_spend_and_history(client):
    dataset_id = client.post("/dataset/load", json={"source": "huggingface", "limit": 10}).json()[
        "dataset_id"
    ]
    client.post(f"/dataset/{dataset_id}/preprocess", json={"steps": ["lowercase"]})
    spend = client.get("/spend").json()
    assert spend["cap_per_day"] > 0 and spend["x_configured"] is False and spend["x_usage"] is None
    runs = client.get("/history").json()
    assert (
        runs and runs[0]["dataset_id"] == dataset_id and runs[0]["applied_steps"] == ["lowercase"]
    )


def test_api_key_protects_every_endpoint_except_health(client, monkeypatch):
    """Without this, anyone who finds the URL can spend the operator's X and AWS budget."""
    from sentiment_prep.config import get_settings

    monkeypatch.setenv("API_KEY", "s3cret")
    get_settings.cache_clear()
    try:
        assert client.get("/health").status_code == 200
        assert client.get("/health").json()["auth_required"] is True
        assert client.get("/steps").status_code == 401
        assert client.get("/steps", headers={"X-API-Key": "wrong"}).status_code == 401
        assert client.get("/steps", headers={"X-API-Key": "s3cret"}).status_code == 200
    finally:
        monkeypatch.delenv("API_KEY")
        get_settings.cache_clear()


def test_api_is_open_when_no_key_is_configured(client):
    """Local development stays frictionless; /health says so."""
    assert client.get("/health").json()["auth_required"] is False
    assert client.get("/steps").status_code == 200


def test_duplicate_posts_are_removed_at_collection_and_reported(client):
    """The dataset a model trains on must not contain the same text twice; the count says so."""
    rows = "text\n" + "".join("the identical viral take on the trial\n" for _ in range(5))
    rows += "a genuinely different opinion about the coverage\n"
    files = {"file": ("d.csv", rows.encode(), "text/csv")}

    summary = client.post("/dataset/upload", files=files).json()

    assert summary["record_count"] == 2
    assert summary["filtered_out"] == {"duplicate": 4}
    report = client.get(f"/dataset/{summary['dataset_id']}/report.md").text
    assert "dropped at collection (duplicate): 4" in report
