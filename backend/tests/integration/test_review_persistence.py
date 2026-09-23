"""Exercise corrected labels through the API, disk reload, and every dataset export."""

import csv
import io

import pyarrow.parquet as pq
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from sentiment_prep.api import deps
from sentiment_prep.api.app import create_app
from sentiment_prep.config import Settings, get_settings
from sentiment_prep.storage.checkpoints import LocalCheckpointStore
from sentiment_prep.storage.local_repository import LocalRepository
from tests.conftest import FakeComprehend


def test_corrected_manual_labels_survive_reload_and_reach_exports(tmp_path, monkeypatch):
    repo = LocalRepository(tmp_path / "work")
    store = LocalCheckpointStore(tmp_path / "checkpoints")
    app = create_app()
    app.dependency_overrides[deps.get_repository] = lambda: repo
    app.dependency_overrides[deps.get_checkpoint_store] = lambda: store
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, diagnostics=True)
    monkeypatch.setattr(deps, "get_comprehend_client", lambda: FakeComprehend())
    monkeypatch.setattr(deps, "get_comprehend_rate", lambda: None)
    client = TestClient(app)
    loaded = client.post(
        "/dataset/upload",
        files={"file": ("posts.csv", b"id,text\na,I LOVED this phone\nb,Not good at all\n")},
    )
    assert loaded.status_code == 200
    dataset_id = loaded.json()["dataset_id"]
    url = f"/dataset/{dataset_id}"
    collected = store.read(dataset_id, "collected", "csv")
    assert client.post(url + "/preprocess", json={"steps": ["lowercase"]}).status_code == 200
    assert (
        client.post(
            url + "/labels/comprehend", json={"max_records": 25, "confirm_cost": True}
        ).status_code
        == 200
    )
    assert (
        client.put(
            url + "/labels/review",
            json={"mode": "low_confidence", "size": 2, "unit": "count", "seed": 7},
        ).status_code
        == 200
    )
    for label in ["negative", "mixed"]:
        result = client.post(url + "/labels/manual", json={"items": [{"id": "a", "label": label}]})
        assert result.status_code == 200
        assert result.json()["reviewed"] == result.json()["manually_reviewed"] == 1
    # A new repository instance reads the persisted choice, not the API's working object.
    app.dependency_overrides[deps.get_repository] = lambda: LocalRepository(tmp_path / "work")
    page = client.get(url + "/labels/review").json()
    assert page["reviewed"] == 1 and page["total"] == 2
    assert page["items"][0]["label"] == "mixed"
    assert store.read(dataset_id, "collected", "csv") == collected
    csv_rows = list(
        csv.DictReader(io.StringIO(client.get(url + "/export.csv").text.lstrip("\ufeff")))
    )
    parquet_rows = pq.read_table(
        io.BytesIO(client.get(url + "/export.parquet").content)
    ).to_pylist()
    checkpoint_rows = list(
        csv.DictReader(io.StringIO(store.read(dataset_id, "labelled", "csv").decode("utf-8-sig")))
    )
    workbook = load_workbook(io.BytesIO(client.get(url + "/export.xlsx").content))
    values = list(workbook["data"].values)
    excel_rows = [dict(zip(values[0], row, strict=True)) for row in values[1:]]
    for rows in [csv_rows, parquet_rows, checkpoint_rows, excel_rows]:
        row = next(r for r in rows if r["id"] == "a")
        assert row["label"] == "mixed" and row["label_source"] == "manual"
        assert row["label_confidence"] in (None, "")
        assert row["comprehend_label"] == "positive" and float(row["comprehend_confidence"]) == 0.85
        assert row["original_text"] == "I LOVED this phone"
        assert row["processed_text"] == "i loved this phone"
    assert "1 of 2 reviewed" in client.get(url + "/report.md").text
