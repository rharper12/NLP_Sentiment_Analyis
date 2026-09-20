"""Routes via TestClient with every external dependency replaced."""

import httpx
import pytest
from fastapi.testclient import TestClient

from sentiment_prep.api import deps
from sentiment_prep.api.app import create_app
from sentiment_prep.sources.huggingface import HuggingFaceSource
from sentiment_prep.storage.repository import InMemoryRepository
from tests.conftest import FakeBedrock, FakeComprehend

REPOSITORY_DEPENDENCY = deps.get_repository
REAL_COMPREHEND_GETTER = deps.get_comprehend_client
REAL_BEDROCK_GETTER = deps.get_bedrock_client
REAL_RATE_GETTER = deps.get_comprehend_rate


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
    app.dependency_overrides[REPOSITORY_DEPENDENCY] = lambda: repo
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
        assert all(k not in body for k in hidden)
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
    # Production-sized vectors may exhaust a request slice while progress is persisted.
    for _ in range(5):
        if not body["partial"]:
            break
        run = client.post(
            f"/dataset/{dataset_id}/preprocess",
            json={"steps": ["lowercase", "tokenize", "stopwords"]},
        )
        assert run.status_code == 200
        body = run.json()
    assert not body["partial"]
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


@pytest.fixture
def operator(client, monkeypatch):
    from sentiment_prep.config import get_settings

    monkeypatch.setenv("API_KEY", "test-only-operator-key")
    get_settings.cache_clear()
    response = client.post("/auth/session", json={"key": "test-only-operator-key"})
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    yield client
    client.headers.pop("Authorization", None)
    get_settings.cache_clear()


def test_operator_session_rejects_anonymous_wrong_tampered_and_expired(operator, monkeypatch):
    from sentiment_prep.api import security

    assert operator.get("/steps").status_code == 200
    assert operator.post("/auth/session", json={"key": "wrong"}).status_code == 401
    assert operator.get("/steps", headers={"Authorization": "Bearer modified"}).status_code == 401
    token = operator.headers["Authorization"]
    assert (
        operator.get(
            "/steps", headers={"Authorization": token[:-1] + ("a" if token[-1] != "a" else "b")}
        ).status_code
        == 401
    )
    assert operator.get("/steps", headers={"Authorization": ""}).status_code == 401
    assert operator.get("/health", headers={"Authorization": ""}).json()["diagnostics"] is False
    expiry = int(token.removeprefix("Bearer ").split(".")[0])
    monkeypatch.setattr(security.time, "time", lambda: expiry + 1)
    assert operator.get("/steps").status_code == 401


def test_invalid_login_never_reflects_credential(operator):
    sentinel = "credential-validation-sentinel-" * 200
    response = operator.post("/auth/session", json={"key": sentinel})
    assert response.status_code == 422 and sentinel not in response.text


def test_lambda_without_configured_authorization_fails_closed(client, monkeypatch):
    from sentiment_prep.config import get_settings

    monkeypatch.setenv("RUNTIME", "lambda")
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setenv("API_KEY_SSM_PATH", "")
    get_settings.cache_clear()
    try:
        assert client.get("/health").json()["auth_required"] is True
        assert client.get("/steps").status_code == 401
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("diagnostics", [False, True])
def test_diagnostics_policy_covers_json_routes_and_exports(
    operator, monkeypatch, diagnostics, s3_bucket
):
    import json
    from io import BytesIO

    from openpyxl import load_workbook

    from sentiment_prep.config import get_settings
    from sentiment_prep.storage.s3_store import S3Store

    monkeypatch.setenv("DIAGNOSTICS", str(diagnostics).lower())
    get_settings.cache_clear()
    monkeypatch.setattr(deps, "get_comprehend_client", lambda: None)
    monkeypatch.setattr(deps, "get_bedrock_client", lambda: None)
    s3, bucket = s3_bucket
    monkeypatch.setattr(deps, "get_s3_store", lambda: S3Store(bucket, "datasets", s3))
    try:
        dataset_id = operator.post(
            "/dataset/upload", files={"file": ("sample.csv", b"text\nA useful opinion here\n")}
        ).json()["dataset_id"]
        root = f"/dataset/{dataset_id}"
        run = operator.post(f"{root}/preprocess", json={"steps": ["lowercase"]})
        assert run.status_code == 200
        assert ("duration_ms" in run.json()["report"]["steps"][0]) is diagnostics
        assert ("COMPREHEND_ENABLED" in run.text) is diagnostics
        assert ("BEDROCK_ENABLED" in run.text) is diagnostics
        assert ("runtime" in operator.get("/health").json()) is diagnostics
        history = operator.get("/history").json()
        assert history and all(("duration_ms" in row) is diagnostics for row in history)
        assert history[0]["sentiment_agreement"] is None
        assert history[0]["embedding_drift"] is None
        listed = operator.get(f"{root}/checkpoints")
        converted = operator.post(f"{root}/checkpoints/processed/parquet")
        assert listed.status_code == converted.status_code == (200 if diagnostics else 404)
        markdown = operator.get(f"{root}/report.md")
        assert markdown.status_code == 200
        assert ("Checkpoint `" in markdown.text) is diagnostics
        assert ("COMPREHEND_ENABLED" in markdown.text) is diagnostics
        workbook = load_workbook(BytesIO(operator.get(f"{root}/export.xlsx").content))
        columns = [cell.value for cell in workbook["impact"][1]]
        assert ("duration_ms" in columns) is diagnostics
        saved = operator.post(f"{root}/save")
        assert saved.status_code == 200 and ("uri" in saved.json()) is diagnostics
        keys = s3.list_objects_v2(Bucket=bucket)["Contents"]
        impact_key = next(k["Key"] for k in keys if k["Key"].endswith("impact.json"))
        impact = json.loads(s3.get_object(Bucket=bucket, Key=impact_key)["Body"].read())
        assert ("duration_ms" in impact["steps"][0]) is diagnostics
        assert ("COMPREHEND_ENABLED" in str(impact)) is diagnostics
        failure = operator.post(f"{root}/labels/comprehend", json={"confirm_cost": True})
        assert failure.status_code == 503
        assert ("COMPREHEND_ENABLED" in failure.text) is diagnostics
        # JSON projection must not erase stored operator facts or prevent ordinary data exports.
        bundle = deps.get_repository().get(dataset_id)
        assert bundle.report.steps[0].duration_ms >= 0
        assert bundle.checkpoints and "COMPREHEND_ENABLED" in str(bundle.report.warnings)
        for suffix in ("csv", "xlsx", "parquet"):
            result = operator.get(f"{root}/export.{suffix}")
            assert result.status_code == 200
            assert f"{dataset_id}.{suffix}" in result.headers["Content-Disposition"]
        assert f"{dataset_id}-report.md" in markdown.headers["Content-Disposition"]
    finally:
        get_settings.cache_clear()


def test_session_authentication_applies_to_every_download(operator, monkeypatch):
    from sentiment_prep.api import security

    dataset_id = operator.post(
        "/dataset/upload", files={"file": ("s.csv", b"text\nhello there\n")}
    ).json()["dataset_id"]
    token = operator.headers["Authorization"]
    for export in ("export.csv", "export.xlsx", "export.parquet", "report.md"):
        path = f"/dataset/{dataset_id}/{export}"
        assert operator.get(path, headers={"Authorization": ""}).status_code == 401
        assert operator.get(path).status_code == 200
    expiry = int(token.removeprefix("Bearer ").split(".")[0])
    monkeypatch.setattr(security.time, "time", lambda: expiry + 1)
    for export in ("export.csv", "export.xlsx", "export.parquet", "report.md"):
        assert operator.get(f"/dataset/{dataset_id}/{export}").status_code == 401


@pytest.mark.parametrize("successful", [False, True])
def test_failed_labels_are_persisted_between_requests(operator, monkeypatch, successful):
    from tests.unit.test_comprehend_failures import FailingComprehend

    fake = FailingComprehend()
    monkeypatch.setattr(deps, "get_comprehend_client", lambda: fake)
    dataset_id = operator.post(
        "/dataset/upload",
        files={
            "file": (
                "s.csv",
                b"text\n"
                + (b"good usable document\n" if successful else b"")
                + b"bad rejected document\n",
            )
        },
    ).json()["dataset_id"]
    for index in range(5):
        result = operator.post(
            f"/dataset/{dataset_id}/labels/comprehend", json={"confirm_cost": True}
        ).json()
        assert result["labelled_in_call"] == (1 if index == 0 and successful else 0)
        assert (
            result["labelled_total"] == int(successful)
            and result["failed_total"] == 1
            and result["done"]
        )
    assert fake.calls == 1


@pytest.mark.parametrize("diagnostics", [False, True])
def test_failed_comparison_remains_unavailable_in_json_and_report(
    operator, monkeypatch, diagnostics
):
    from sentiment_prep.config import get_settings
    from tests.unit.test_comprehend_failures import FailingComprehend

    fake = FailingComprehend()
    monkeypatch.setenv("DIAGNOSTICS", str(diagnostics).lower())
    get_settings.cache_clear()
    monkeypatch.setattr(deps, "get_comprehend_client", lambda: fake)
    monkeypatch.setattr(deps, "get_bedrock_client", lambda: None)
    try:
        dataset_id = operator.post(
            "/dataset/upload", files={"file": ("s.csv", b"text\nbad rejected document\n")}
        ).json()["dataset_id"]
        result = operator.post(
            f"/dataset/{dataset_id}/preprocess", json={"steps": ["lowercase"]}
        ).json()
        assert result["report"]["sentiment"]["agreement"] is None
        assert result["report"]["sentiment"]["comparable_records"] == 0
        assert result["report"]["sentiment"]["shared_records"] == 1
        markdown = operator.get(f"/dataset/{dataset_id}/report.md").text
        assert (
            "**unavailable** over 0 successful comparable records of 1 shared records" in markdown
        )
        assert fake.calls == 1
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("diagnostics", [False, True])
def test_raw_service_usage_is_operator_only(operator, monkeypatch, diagnostics):
    from unittest.mock import Mock

    from sentiment_prep.config import get_settings

    source = Mock()
    source.usage.return_value = {"operator_account": "test-only-account"}
    monkeypatch.setenv("X_BEARER_TOKEN", "test-only-token")
    monkeypatch.setenv("DIAGNOSTICS", str(diagnostics).lower())
    get_settings.cache_clear()
    monkeypatch.setattr(deps, "get_x_source", lambda: source)
    try:
        response = operator.get("/spend?include_x_usage=true")
        assert response.status_code == 200
        assert ("test-only-account" in response.text) is diagnostics
        assert source.usage.call_count == int(diagnostics)
    finally:
        get_settings.cache_clear()


def test_duplicate_uploaded_ids_are_rejected_before_collection(client):
    response = client.post(
        "/dataset/upload",
        files={"file": ("duplicate.csv", b"id,text\nsame,one opinion\nsame,a different opinion\n")},
    )
    assert response.status_code == 400
    assert "duplicate record id 'same' at CSV row 3" in response.json()["error"]


@pytest.mark.parametrize("failure", ["profile", "credentials", "session", "client", "region"])
@pytest.mark.parametrize("stage", ["sentiment", "embedding", "explanation"])
def test_optional_aws_initialization_keeps_cleaning_and_local_metrics(
    client, monkeypatch, failure, stage
):
    from botocore.exceptions import (
        CredentialRetrievalError,
        InvalidRegionError,
        NoCredentialsError,
        ProfileNotFound,
    )

    from sentiment_prep.config import Settings

    errors = {
        "profile": ProfileNotFound(profile="missing"),
        "credentials": NoCredentialsError(),
        "session": CredentialRetrievalError(provider="test", error_msg="failure"),
        "client": RuntimeError("client factory failed"),
        "region": InvalidRegionError(region_name="invalid region"),
    }
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise errors[failure]

    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: Settings(_env_file=None, comprehend_enabled=True, bedrock_enabled=True),
    )
    if failure in ("profile", "session"):
        monkeypatch.setattr(deps, "boto_session", fail)
    else:
        monkeypatch.setattr(deps, "_boto_client", fail)
    # Restore the real getter for the failing service, so initialization itself is exercised.
    if stage == "sentiment":
        monkeypatch.setattr(deps, "get_comprehend_client", REAL_COMPREHEND_GETTER)
    else:
        real = REAL_BEDROCK_GETTER
        attempts = [0]

        def bedrock():
            attempts[0] += 1
            return FakeBedrock() if stage == "explanation" and attempts[0] == 1 else real()

        monkeypatch.setattr(deps, "get_bedrock_client", bedrock)
    loaded = client.post("/dataset/load", json={"source": "huggingface", "limit": 1}).json()
    result = client.post(
        f"/dataset/{loaded['dataset_id']}/preprocess", json={"steps": ["lowercase"]}
    )
    assert result.status_code == 200 and calls
    body = result.json()
    assert body["record_count"] == body["metrics_after"]["record_count"] == 1
    assert any(f"{stage} unavailable" in w for w in body["report"]["warnings"])
    assert (
        body["report"][
            {
                "sentiment": "sentiment",
                "embedding": "embedding_drift",
                "explanation": "explanation",
            }[stage]
        ]
        is None
    )
    assert (
        client.get(f"/dataset/{loaded['dataset_id']}/records")
        .json()["items"][0]["processed"]["text"]
        .islower()
    )


@pytest.mark.parametrize("failure", ["profile", "credentials", "region", "client"])
def test_required_label_client_init_returns_controlled_failure(client, monkeypatch, failure):
    from botocore.exceptions import InvalidRegionError, NoCredentialsError, ProfileNotFound

    errors = {
        "profile": ProfileNotFound(profile="missing"),
        "credentials": NoCredentialsError(),
        "region": InvalidRegionError(region_name="invalid"),
        "client": RuntimeError("client failed"),
    }

    def fail():
        raise errors[failure]

    monkeypatch.setattr(deps, "get_comprehend_client", fail)
    loaded = client.post("/dataset/load", json={"source": "huggingface", "limit": 1}).json()
    root = f"/dataset/{loaded['dataset_id']}"
    result = client.post(f"{root}/labels/comprehend", json={"confirm_cost": True})
    assert result.status_code == 503 and "AWS" in result.text
    records = client.get(f"{root}/records").json()["items"]
    assert records[0]["original"]["comprehend_label"] is None


def test_complete_pricing_failure_does_not_block_labeling(client, monkeypatch):
    from sentiment_prep.history import services as history

    def fail(*args, **kwargs):
        raise RuntimeError("pricing unavailable")

    monkeypatch.setattr(history, "get_price_quote", fail)
    monkeypatch.setattr(deps, "_boto_client", fail)
    monkeypatch.setattr(deps, "get_comprehend_rate", REAL_RATE_GETTER)
    loaded = client.post("/dataset/load", json={"source": "huggingface", "limit": 1}).json()
    root = f"/dataset/{loaded['dataset_id']}"
    quoted = client.get(f"{root}/labels/estimate")
    assert quoted.status_code == 200
    assert quoted.json()["price_status"] == "unavailable"
    assert quoted.json()["estimated_cost_usd"] is quoted.json()["cost_per_unit_usd"] is None
    result = client.post(f"{root}/labels/comprehend", json={"confirm_cost": True})
    assert result.status_code == 200
    assert result.json()["labelled_in_call"] == 1 and result.json()["cost_usd"] is None
