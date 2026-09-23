"""Exercise named local files and exports through the real API without external services."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from sentiment_prep import filenames
from sentiment_prep.api import deps
from sentiment_prep.api.app import create_app
from sentiment_prep.config import Settings, get_settings
from sentiment_prep.storage.checkpoints import LocalCheckpointStore
from sentiment_prep.storage.local_repository import LocalRepository
from sentiment_prep.storage.s3_store import S3Store

STEM = "csv-import-2026-09-23_12-15-30-UTC-0500"


@pytest.fixture
def named_client(tmp_path, monkeypatch):
    class FixedDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 23, 17, 15, 30, tzinfo=UTC)

    monkeypatch.setattr(filenames, "datetime", FixedDate)
    repo = LocalRepository(tmp_path / "_work")
    app = create_app()
    app.dependency_overrides[deps.get_repository] = lambda: repo
    app.dependency_overrides[deps.get_checkpoint_store] = lambda: LocalCheckpointStore(tmp_path)
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, checkpoint_dir=str(tmp_path), comprehend_enabled=False, diagnostics=True
    )
    client = TestClient(app)
    response = client.post(
        "/dataset/upload",
        files={"file": ("posts.csv", b"text\nA good phone for this project\n")},
        headers={"X-Time-Zone": "America/Chicago"},
    )
    assert response.status_code == 200, response.text
    return client, repo, tmp_path, response.json()


def test_named_local_file_restore_and_repeat_saves_keep_identity(named_client):
    client, repo, root, summary = named_client
    dataset_id = summary["dataset_id"]
    assert summary["file_stem"] == STEM
    path = root / "_work" / dataset_id / f"{STEM}.json"
    before = path.read_bytes()
    assert client.get("/local-datasets").json()["items"][0]["filename"] == f"{STEM}.json"
    restored = client.post(
        f"/local-datasets/{dataset_id}/restore", headers={"X-Time-Zone": "America/Chicago"}
    ).json()
    assert restored["dataset_id"] != dataset_id
    assert restored["file_stem"] == STEM
    assert path.read_bytes() == before
    assert (root / "_work" / restored["dataset_id"] / f"{STEM}.json").is_file()
    assert client.post(f"/dataset/{dataset_id}/preprocess", json={"steps": []}).status_code == 200
    assert repo.get(dataset_id).file_stem == STEM
    assert list(path.parent.glob("*.json")) == [path]


@pytest.mark.parametrize("kind", ["csv", "xlsx", "parquet", "md"])
def test_exports_keep_content_and_type_when_renamed(named_client, kind):
    client, _, _, summary = named_client
    base = f"/dataset/{summary['dataset_id']}"
    assert client.post(base + "/preprocess", json={"steps": []}).status_code == 200
    route = base + ("/report.md" if kind == "md" else f"/export.{kind}")
    default = client.get(route)
    renamed = client.get(route, params={"filename": "My project results"})
    assert default.status_code == renamed.status_code == 200
    assert default.headers["Content-Disposition"] == f'attachment; filename="{STEM}.{kind}"'
    assert (
        renamed.headers["Content-Disposition"]
        == f'attachment; filename="My project results.{kind}"'
    )
    assert default.headers["Content-Type"] == renamed.headers["Content-Type"]
    # Excel ZIP metadata can contain creation times; the other serializers are byte-stable.
    if kind != "xlsx":
        assert default.content == renamed.content
    assert (
        client.get(base + "/original.json").headers["Content-Disposition"]
        == f'attachment; filename="{STEM}.json"'
    )


@pytest.mark.parametrize(
    "name", ["", "../escape", "file.csv", 'bad"name', "line\r\nbreak", "a" * 121]
)
def test_invalid_names_are_rejected_before_download_or_s3(named_client, monkeypatch, name):
    client, _, _, summary = named_client

    def unexpected():
        pytest.fail("Invalid filenames must not access S3")

    monkeypatch.setattr(deps, "get_s3_store", unexpected)
    base = f"/dataset/{summary['dataset_id']}"
    for route in ["export.csv", "export.xlsx", "export.parquet", "report.md"]:
        assert client.get(f"{base}/{route}", params={"filename": name}).status_code == 422
    assert client.post(base + "/save", params={"filename": name}).status_code == 422


def test_s3_uses_the_same_name_and_never_overwrites_a_previous_save(
    named_client, s3_bucket, monkeypatch
):
    client, repo, _, summary = named_client
    s3, bucket = s3_bucket
    monkeypatch.setattr(deps, "get_s3_store", lambda: S3Store(bucket, "datasets", s3))
    base = f"/dataset/{summary['dataset_id']}/save"
    first = client.post(base).json()["uri"]
    second = client.post(base).json()["uri"]
    custom = client.post(base, params={"filename": "My results"}).json()["uri"]
    assert first != second != custom
    keys = [obj["Key"] for obj in s3.list_objects_v2(Bucket=bucket)["Contents"]]
    assert sum(key.endswith(f"/{STEM}.parquet") for key in keys) == 2
    assert sum(key.endswith("/My results.parquet") for key in keys) == 1
    assert repo.get(summary["dataset_id"]).file_stem == STEM


def test_bad_timezone_is_rejected_before_collecting(named_client):
    client, _, _, _ = named_client
    response = client.post(
        "/dataset/upload",
        files={"file": ("posts.csv", b"text\npost\n")},
        headers={"X-Time-Zone": "not/a/zone"},
    )
    assert response.status_code == 400
