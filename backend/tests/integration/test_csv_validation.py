"""Preflight validates all content without persisting a dataset or contacting providers."""

import pytest
from fastapi.testclient import TestClient

from sentiment_prep.api import deps
from sentiment_prep.api.app import create_app


@pytest.fixture
def client(monkeypatch):
    def unexpected():
        pytest.fail("CSV preflight must not touch repositories, checkpoints, or providers")

    app = create_app()
    for dependency in [deps.get_repository, deps.get_checkpoint_store]:
        app.dependency_overrides[dependency] = unexpected
    monkeypatch.setattr(deps, "get_comprehend_client", unexpected)
    return TestClient(app)


def test_preflight_reports_valid_rows_labels_and_empty_rows(client):
    content = '\ufeff Text ,ID,label\n"Good camera, bright screen",one,Positive\n"Line one\nLine two",two,\n,three,negative\n'
    response = client.post(
        "/dataset/upload/validate", files={"file": ("posts.csv", content.encode())}
    )
    assert response.status_code == 200
    result = response.json()
    assert (result["record_count"], result["skipped_empty"], result["labelled_count"]) == (2, 1, 1)
    assert result["preview"][0]["label"] == "positive"
    assert result["preview"][1]["text"] == "Line one\nLine two"


@pytest.mark.parametrize(
    "content", [b"text\n", b"text,id\n ,a\n", b"id\na\n", b"text,id\na,same\nb,same\n"]
)
def test_empty_or_unusable_csv_cannot_be_marked_ready(client, content):
    response = client.post("/dataset/upload/validate", files={"file": ("posts.csv", content)})
    assert response.status_code == 400


def test_preflight_checks_rows_after_its_preview(client):
    content = b"text,id\n" + b"".join(f"post {i},{i}\n".encode() for i in range(50)) + b"last,49\n"
    response = client.post("/dataset/upload/validate", files={"file": ("posts.csv", content)})
    assert response.status_code == 400 and "duplicate record id" in response.json()["error"]
