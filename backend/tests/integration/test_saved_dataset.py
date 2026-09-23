"""Original-data round trips must never resume paid work or reuse derived state."""

import json
import os

import pytest
from fastapi.testclient import TestClient

from sentiment_prep.api import deps
from sentiment_prep.api.app import create_app
from sentiment_prep.config import Settings, get_settings
from sentiment_prep.models import Dataset, DatasetBundle, Record
from sentiment_prep.sources.csv_upload import MAX_UPLOAD_RECORDS
from sentiment_prep.storage.checkpoints import LocalCheckpointStore
from sentiment_prep.storage.local_repository import LocalRepository


@pytest.fixture
def saved_client(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Restoring original data must not call a source or ML service")

    for name in ["get_x_source", "get_hf_source", "get_comprehend_client"]:
        monkeypatch.setattr(deps, name, unexpected)
    repo = LocalRepository(tmp_path / "_work")
    original = Dataset(
        source_type="x",
        query='"example" lang:en',
        window_start="2026-09-09T00:00:00Z",
        window_end="2026-09-21T00:00:00Z",
        records=[
            Record(
                id="one",
                text="  I don\u2019t LOVE this! 🔥\nhttps://t.co/AbC  ",
                source_type="x",
                created_at="2026-09-10T00:00:00Z",
                label="negative",
                label_source="comprehend",
                comprehend_label="negative",
                comprehend_confidence=0.9,
            ),
            Record(
                id="two", text="same text", source_type="x", label="neutral", label_source="source"
            ),
            Record(id="three", text="same text", source_type="x"),
            Record(id="empty", text="", source_type="x"),
        ],
    )
    bundle = DatasetBundle(
        dataset_id="existing",
        original=original,
        processed=Dataset(
            source_type="x",
            records=[Record(id="one", text="WRONG PROCESSED TEXT", source_type="x")],
        ),
        applied_steps=["lowercase"],
        review_ids=["one"],
    )
    repo.save(bundle)
    app = create_app()
    app.dependency_overrides[deps.get_repository] = lambda: repo
    app.dependency_overrides[deps.get_checkpoint_store] = lambda: LocalCheckpointStore(tmp_path)
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, checkpoint_dir=str(tmp_path)
    )
    return TestClient(app), repo, tmp_path


def test_local_restore_starts_fresh_without_changing_saved_rows(saved_client):
    client, repo, root = saved_client
    before = (root / "_work/existing.json").read_bytes()
    bundle = repo.get("existing")
    response = client.post("/local-datasets/existing/restore")
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["record_count"] == 4
    assert summary["partial"] is False and summary["billed_reads"] is None
    restored = repo.get(summary["dataset_id"])
    assert restored.dataset_id != "existing"
    assert [r.text for r in restored.original.records] == [r.text for r in bundle.original.records]
    assert [r.id for r in restored.original.records] == [r.id for r in bundle.original.records]
    assert restored.original.records[0].created_at == bundle.original.records[0].created_at
    assert restored.original.records[0].label is None
    assert restored.original.records[0].comprehend_label is None
    assert restored.original.records[1].label == "neutral"
    assert restored.original.records[1].label_source == "source"
    assert restored.processed is None and restored.report is None
    assert restored.applied_steps == [] and restored.review_ids == []
    assert restored.collection is None and restored.analysis.sentiment == {}
    assert restored.original.query == bundle.original.query
    assert restored.original.window_start == bundle.original.window_start
    assert restored.original.fetched_at == bundle.original.fetched_at
    assert (root / "_work/existing.json").read_bytes() == before


def test_download_remains_an_original_data_archive(saved_client):
    client, _, _ = saved_client
    response = client.get("/dataset/existing/original.json")
    assert response.status_code == 200
    assert set(response.json()) == {"format", "original"}
    assert response.json()["original"]["records"][0]["comprehend_label"] is None


def test_local_catalog_orders_metadata_and_pages_without_loading_contents(
    saved_client, monkeypatch
):
    client, _, root = saved_client
    folder = root / "_work"
    (folder / "newest.json").write_text("{}")
    os.utime(folder / "newest.json", (200, 200))
    os.utime(folder / "existing.json", (100, 100))
    (folder / "ignore.tmp").write_text("temp")
    (folder / "linked.json").symlink_to(folder / "existing.json")
    (folder / "directory.json").mkdir()

    def unexpected(*args):
        pytest.fail("The picker must not deserialize every dataset")

    monkeypatch.setattr(LocalRepository, "get", unexpected)
    first = client.get("/local-datasets?limit=1").json()
    second = client.get("/local-datasets?offset=1&limit=1").json()
    assert first["total"] == 2
    assert first["items"][0]["filename"] == "newest.json"
    assert second["items"][0]["filename"] == "existing.json"
    assert "Cache-Control" in client.get("/local-datasets").headers
    assert first["items"][0]["bytes"] == 2


@pytest.mark.parametrize("runtime,bucket", [("lambda", None), ("lambda", "test-bucket")])
def test_local_picker_is_not_available_in_other_runtimes(saved_client, runtime, bucket):
    client, _, root = saved_client
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        runtime=runtime,
        data_bucket=bucket,
        diagnostics=True,
        api_key="test-key",
        checkpoint_dir=str(root),
    )
    headers = {"X-API-Key": "test-key"}
    assert client.get("/local-datasets", headers=headers).status_code == 404
    assert client.post("/local-datasets/existing/restore", headers=headers).status_code == 404
    assert client.get("/health", headers=headers).json()["local_datasets_available"] is False


@pytest.mark.parametrize("content", ["not json", "{}", '{"original":{"records":[]}}'])
def test_invalid_local_file_is_rejected_without_creating_a_dataset(saved_client, content):
    client, _, root = saved_client
    (root / "_work/broken.json").write_text(content)
    before = set((root / "_work").iterdir())
    response = client.post("/local-datasets/broken/restore")
    assert response.status_code == 400
    assert "valid local dataset JSON" in response.json()["error"]
    assert set((root / "_work").iterdir()) == before


def test_restore_cannot_follow_symlinks_or_use_paths(saved_client):
    client, _, root = saved_client
    (root / "_work/linked.json").symlink_to(root / "_work/existing.json")
    assert client.post("/local-datasets/linked/restore").status_code == 400
    assert client.post("/local-datasets/../restore").status_code in (400, 404)
    assert client.post("/local-datasets/missing/restore").status_code == 404
    assert client.post("/dataset/restore", files={"file": ("data.json", b"{}")}).status_code in (
        404,
        405,
    )


def test_restore_refuses_record_limit_instead_of_truncating(saved_client):
    client, repo, root = saved_client
    payload = repo.get("existing").model_dump(mode="json")
    payload["original"]["records"] = [
        {"id": str(i), "text": "text", "source_type": "csv"} for i in range(MAX_UPLOAD_RECORDS + 1)
    ]
    (root / "_work/existing.json").write_text(json.dumps(payload))
    response = client.post("/local-datasets/existing/restore")
    assert response.status_code == 400
    assert "5,000" in response.json()["error"]
