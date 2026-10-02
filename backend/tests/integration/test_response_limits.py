"""Large accepted uploads remain pageable and downloadable through the Lambda adapter."""

import asyncio
import base64
import csv
import io
import json
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

import pytest
from fastapi.testclient import TestClient
from mangum import Mangum

from sentiment_prep.api import deps
from sentiment_prep.api.app import create_app
from sentiment_prep.api.downloads import DOWNLOAD_LINK_MEDIA_TYPE
from sentiment_prep.api.pagination import ANALYSIS_PREVIEW_WARNING
from sentiment_prep.config import Settings, get_settings
from sentiment_prep.storage.checkpoints import LocalCheckpointStore
from sentiment_prep.storage.repository import InMemoryRepository
from sentiment_prep.storage.s3_store import S3Store


@pytest.fixture
def large_dataset(tmp_path):
    repo = InMemoryRepository()
    settings = Settings(
        _env_file=None, dedupe_enabled=False, comprehend_enabled=False, diagnostics=False
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[deps.get_repository] = lambda: repo
    app.dependency_overrides[deps.get_checkpoint_store] = lambda: LocalCheckpointStore(tmp_path)
    client = TestClient(app)
    content = io.StringIO()
    writer = csv.writer(content)
    writer.writerow(["id", "text"])
    for i in range(500):
        writer.writerow([str(i), (f"WORD{i:04d} " * 875).strip()])
    raw = content.getvalue().encode()
    assert len(raw) < 4 * 1024 * 1024
    uploaded = client.post("/dataset/upload", files={"file": ("large.csv", raw)})
    assert uploaded.status_code == 200
    assert uploaded.json()["record_count"] == 500
    identifier = uploaded.json()["dataset_id"]
    response = client.post(f"/dataset/{identifier}/preprocess", json={"steps": ["lowercase"]})
    assert response.status_code == 200
    return app, client, identifier


def invoke(app, path, query="", *, method="GET", payload=None):
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": query,
        "headers": {
            "host": "localhost",
            "x-api-key": "test-key",
            "content-type": "application/json",
        },
        "requestContext": {
            "http": {
                "method": method,
                "path": path,
                "sourceIp": "127.0.0.1",
                "protocol": "HTTP/1.1",
            },
            "stage": "$default",
        },
        "isBase64Encoded": False,
        "body": json.dumps(payload) if payload is not None else None,
    }
    try:
        previous_loop = asyncio.get_event_loop()
    except RuntimeError:
        previous_loop = None
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = Mangum(app, lifespan="off")(event, SimpleNamespace(aws_request_id="response-test"))
    finally:
        loop.close()
        asyncio.set_event_loop(previous_loop)
    assert result["statusCode"] == 200
    assert len(json.dumps(result).encode()) < 6 * 1024 * 1024
    body = base64.b64decode(result["body"]) if result["isBase64Encoded"] else result["body"]
    return result, json.loads(body)


def test_records_pages_fit_lambda_and_cover_every_original_and_processed_row(large_dataset):
    app, _, identifier = large_dataset
    records = []
    while len(records) < 500:
        _, page = invoke(app, f"/dataset/{identifier}/records", f"offset={len(records)}&limit=1000")
        assert page["total"] == 500 and page["offset"] == len(records)
        assert 0 < len(page["items"]) < 500
        records.extend(page["items"])
    assert [r["original"]["id"] for r in records] == [str(i) for i in range(500)]
    assert all(r["processed"]["text"] == r["original"]["text"].lower() for r in records)


@pytest.mark.parametrize(
    "kind", ["export.csv", "export.xlsx", "export.parquet", "original.json", "report.md"]
)
def test_cloud_download_preserves_local_export_bytes_without_a_large_lambda_response(
    large_dataset, s3_bucket, monkeypatch, kind
):
    app, client, identifier = large_dataset
    path = f"/dataset/{identifier}/{kind}"
    local = client.get(path)
    assert local.status_code == 200
    if kind == "export.csv":
        assert len(local.content) > 6 * 1024 * 1024
    s3, bucket = s3_bucket
    monkeypatch.setattr(deps, "get_s3_store", lambda: S3Store(bucket, "datasets", s3))
    settings = Settings(_env_file=None, runtime="lambda", data_bucket=bucket, api_key="test-key")
    app.dependency_overrides[get_settings] = lambda: settings
    response, link = invoke(app, path)
    assert response["headers"]["content-type"] == DOWNLOAD_LINK_MEDIA_TYPE
    assert response["headers"]["cache-control"] == "no-store"
    assert link["expires_in"] == 300 and urlparse(link["url"]).scheme == "https"
    key = unquote(urlparse(link["url"]).path).lstrip("/")
    assert key.startswith("_downloads/")
    saved = s3.get_object(Bucket=bucket, Key=key)
    assert saved["ContentDisposition"] == local.headers["content-disposition"]
    content = saved["Body"].read()
    if kind != "export.xlsx":  # ZIP timestamps can change between invocations.
        assert content == local.content
    else:
        from openpyxl import load_workbook

        expected = load_workbook(io.BytesIO(local.content))
        actual = load_workbook(io.BytesIO(content))
        assert list(actual["data"].values) == list(expected["data"].values)


def test_excel_limit_is_an_actionable_api_error(tmp_path):
    app = create_app()
    app.dependency_overrides[deps.get_repository] = lambda: repo
    app.dependency_overrides[deps.get_checkpoint_store] = lambda: LocalCheckpointStore(tmp_path)
    repo = InMemoryRepository()
    client = TestClient(app)
    uploaded = client.post(
        "/dataset/upload", files={"file": ("long.csv", b"text\n" + b"x" * 40000)}
    )
    assert uploaded.status_code == 200
    identifier = uploaded.json()["dataset_id"]
    response = client.get(f"/dataset/{identifier}/export.xlsx")
    assert response.status_code == 400
    assert "Download CSV or Parquet" in response.json()["error"]


@pytest.mark.parametrize("diagnostics", [False, True])
@pytest.mark.parametrize("long_text", [False, True])
def test_complete_analysis_response_fits_lambda_without_changing_saved_text_or_counts(
    tmp_path, diagnostics, long_text
):
    app = create_app()
    repo = InMemoryRepository()
    app.dependency_overrides[deps.get_repository] = lambda: repo
    app.dependency_overrides[deps.get_checkpoint_store] = lambda: LocalCheckpointStore(tmp_path)
    client = TestClient(app)
    texts = [f"TOKEN{i:02d}" + "x" * (129990 if long_text else 3) for i in range(20)]
    content = io.StringIO()
    writer = csv.writer(content)
    writer.writerow(["id", "text"])
    writer.writerows(enumerate(texts))
    raw = content.getvalue().encode()
    assert len(raw) < 4 * 1024 * 1024
    uploaded = client.post("/dataset/upload", files={"file": ("tokens.csv", raw)})
    assert uploaded.status_code == 200 and uploaded.json()["record_count"] == 20
    identifier = uploaded.json()["dataset_id"]

    settings = Settings(
        _env_file=None,
        runtime="lambda",
        api_key="test-key",
        diagnostics=diagnostics,
        comprehend_enabled=False,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    _, result = invoke(
        app, f"/dataset/{identifier}/preprocess", method="POST", payload={"steps": ["lowercase"]}
    )
    assert result["record_count"] == 20 and result["partial"] is False
    for side in ("metrics_before", "metrics_after"):
        assert (
            result[side]["record_count"]
            == result[side]["vocab_size"]
            == result[side]["total_tokens"]
            == 20
        )
        assert result[side]["avg_tokens"] == result[side]["type_token_ratio"] == 1
        assert len(result[side]["top_terms"]) == (0 if long_text else 15)
    step = result["report"]["steps"][0]
    assert (
        step["records_in"]
        == step["records_out"]
        == step["vocab_before"]
        == step["vocab_after"]
        == 20
    )
    assert ("duration_ms" in step) is diagnostics
    assert len(step["sample_diffs"]) == (0 if long_text else 5)
    assert len(result["preview"]) == (0 if long_text else 20)
    assert (ANALYSIS_PREVIEW_WARNING in result["warnings"]) is long_text

    bundle = repo.get(identifier)
    assert [record.text for record in bundle.original.records] == texts
    assert [record.text for record in bundle.processed.records] == [text.lower() for text in texts]
    assert bundle.report.steps[0].sample_diffs == list(
        zip(texts[:5], [text.lower() for text in texts[:5]], strict=True)
    )
    assert ANALYSIS_PREVIEW_WARNING not in bundle.report.warnings
    # Records and exports remain complete after compacting only the HTTP analysis projection.
    _, page = invoke(app, f"/dataset/{identifier}/records", "limit=1")
    assert page["items"][0]["original"]["text"] == texts[0]
    assert page["items"][0]["processed"]["text"] == texts[0].lower()
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, runtime="local")
    exported = client.get(f"/dataset/{identifier}/export.csv")
    rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert [row["original_text"] for row in rows] == texts
    assert [row["processed_text"] for row in rows] == [text.lower() for text in texts]


@pytest.mark.parametrize("character", ["\x01", "\x0b", "\x0e", "\x1f", "\ufffe", "\uffff"])
def test_excel_invalid_character_returns_validation_error_and_keeps_lossless_exports(
    tmp_path, character
):
    app = create_app()
    repo = InMemoryRepository()
    app.dependency_overrides[deps.get_repository] = lambda: repo
    app.dependency_overrides[deps.get_checkpoint_store] = lambda: LocalCheckpointStore(tmp_path)
    client = TestClient(app)
    text = f"hello{character}world"
    uploaded = client.post(
        "/dataset/upload", files={"file": ("control.csv", f"text\n{text}\n".encode())}
    )
    assert uploaded.status_code == 200
    identifier = uploaded.json()["dataset_id"]
    response = client.get(f"/dataset/{identifier}/export.xlsx")
    assert response.status_code == 400
    message = response.json()["error"]
    assert f"U+{ord(character):04X}" in message and "row 2, original_text" in message
    assert "Download CSV or Parquet" in message and text not in message
    csv_response = client.get(f"/dataset/{identifier}/export.csv")
    assert csv_response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
    assert rows[0]["original_text"] == text
    assert client.get(f"/dataset/{identifier}/export.parquet").status_code == 200
    assert repo.get(identifier).original.records[0].text == text
