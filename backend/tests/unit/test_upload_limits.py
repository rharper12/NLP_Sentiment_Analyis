"""Content and streaming limits apply independently of browser metadata."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from sentiment_prep.api.app import create_app
from sentiment_prep.api.upload_limit import MAX_UPLOAD_BODY_BYTES, UploadLimitMiddleware
from sentiment_prep.sources.csv_upload import MAX_UPLOAD_BYTES


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.mark.parametrize("limit", [0, -1, 5001, "invalid"])
def test_invalid_limits_are_controlled(client, limit):
    response = client.post(
        f"/dataset/upload?limit={limit}", files={"file": ("a.csv", b"text\nhello\n")}
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "content",
    [
        b"text\n\xff",
        b'text\n"unterminated',
        b'text\n"quoted"junk\n',
        b"text,TEXT\none,two\n",
        b"text, text \none,two\n",
        b"text,\na,b\n",
        b"text,id\nmissing id\n",
        b"text\na,b\n",
        b"text\n\x00binary\n",
        b"text\n" + b"a" * 140000,
    ],
)
def test_bad_content_returns_4xx(client, content):
    response = client.post("/dataset/upload", files={"file": ("innocent.csv", content, "text/csv")})
    assert response.status_code == 400


def test_bytes_and_rows_cannot_be_bypassed(client):
    too_large = b"text\n" + b"hello\n" * (MAX_UPLOAD_BYTES // 6 + 1)
    assert client.post("/dataset/upload", files={"file": ("a.csv", too_large)}).status_code == 413
    rows = b"text\n" + b"hello\n" * 5001
    # Even a small requested preview must validate the full file's server row limit.
    assert (
        client.post("/dataset/upload?limit=1", files={"file": ("a.csv", rows)}).status_code == 400
    )


def test_csv_content_is_authoritative_and_truncation_is_explicit(client):
    response = client.post(
        "/dataset/upload?limit=1",
        files={"file": ("data.bin", b"text\nhello\nworld\n", "application/octet-stream")},
    )
    assert response.status_code == 200
    assert response.json()["record_count"] == 1
    assert "omitted 1" in response.json()["truncated_reason"]
    assert (
        client.post(
            "/dataset/upload", files={"file": ("data.csv", b"not a csv", "text/csv")}
        ).status_code
        == 400
    )


def test_body_is_bounded_before_multipart_parsing_without_content_length():
    calls = 0
    messages = []

    async def receive():
        nonlocal calls
        calls += 1
        return {"type": "http.request", "body": b"x" * 65536, "more_body": True}

    async def parser(scope, receive, send):
        while True:
            await receive()

    async def send(message):
        messages.append(message)

    asyncio.run(
        UploadLimitMiddleware(parser)({"type": "http", "path": "/dataset/upload"}, receive, send)
    )
    assert calls == MAX_UPLOAD_BODY_BYTES // 65536 + 1
    assert messages[0]["status"] == 413
