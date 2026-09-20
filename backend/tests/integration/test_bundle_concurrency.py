"""Deterministic API races: reject overlapping edits before they can change or bill anything."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from fastapi.testclient import TestClient

from sentiment_prep.api import deps, routes
from sentiment_prep.api.app import create_app
from sentiment_prep.config import Settings, get_settings
from sentiment_prep.errors import ConflictError
from sentiment_prep.models import DatasetBundle
from sentiment_prep.storage.checkpoints import LocalCheckpointStore
from sentiment_prep.storage.repository import InMemoryRepository, S3Repository
from tests.conftest import FakeComprehend, make_dataset


@pytest.fixture(params=["local", "s3"])
def setup(request, s3_bucket, tmp_path, monkeypatch):
    s3, bucket = s3_bucket
    local = InMemoryRepository()
    factory = (lambda: local) if request.param == "local" else (lambda: S3Repository(bucket, s3))
    app = create_app()
    settings = Settings(
        _env_file=None,
        api_key="concurrency-test",
        api_key_ssm_path=None,
        runtime="local",
        diagnostics=False,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[deps.get_repository] = factory
    app.dependency_overrides[deps.get_checkpoint_store] = lambda: LocalCheckpointStore(tmp_path)
    monkeypatch.setattr(deps, "get_bedrock_client", lambda: None)
    monkeypatch.setattr(deps, "get_comprehend_rate", lambda: None)
    fake = FakeComprehend()
    monkeypatch.setattr(deps, "get_comprehend_client", lambda: fake)
    client = TestClient(app, headers={"X-API-Key": "concurrency-test"})
    response = client.post(
        "/dataset/upload",
        files={
            "file": ("records.csv", b"id,text\na,I LOVED IT\nb,absolutely terrible experience\n")
        },
    )
    assert response.status_code == 200
    return client, factory, response.json()["dataset_id"], fake


@pytest.mark.parametrize("owner", ["manual", "preprocess", "comprehend"])
def test_concurrent_writes_conflict_and_retry_preserves_changes(setup, monkeypatch, owner):
    client, factory, dataset_id, fake = setup
    entered, release = Event(), Event()
    root = f"/dataset/{dataset_id}"
    if owner == "manual":
        original = routes.labeling.apply_manual_labels
        target, body = "/labels/manual", {"items": [{"id": "a", "label": "mixed"}]}
    elif owner == "preprocess":
        original = routes.run_preprocessing
        target, body = "/preprocess", {"steps": ["lowercase"], "explain": False}
    else:
        original = fake.batch_detect_sentiment
        target, body = "/labels/comprehend", {"confirm_cost": True}

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10), "test must release the winning operation"
        return original(*args, **kwargs)

    if owner == "manual":
        monkeypatch.setattr(routes.labeling, "apply_manual_labels", blocked)
    elif owner == "preprocess":
        monkeypatch.setattr(routes, "run_preprocessing", blocked)
    else:
        monkeypatch.setattr(fake, "batch_detect_sentiment", blocked)

    competing_target = target if owner == "comprehend" else "/labels/manual"
    competing_body = body if owner == "comprehend" else {"items": [{"id": "b", "label": "neutral"}]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, root + target, json=body)
        try:
            assert entered.wait(10)
            rejected = client.post(root + competing_target, json=competing_body)
            assert rejected.status_code == 409
            assert "being edited" in rejected.json()["error"]
        finally:
            release.set()
        assert first.result(timeout=10).status_code == 200
    assert client.post(root + competing_target, json=competing_body).status_code == 200
    saved = factory().get(dataset_id)
    if owner == "manual":
        assert [r.label for r in saved.original.records] == ["mixed", "neutral"]
    elif owner == "preprocess":
        assert saved.processed.records[0].text == "i loved it"
        assert saved.original.records[1].label == "neutral"
        assert saved.applied_steps == ["lowercase"]
    else:
        assert fake.calls == 1  # The rejected and retried caller never re-bills pending records.
        assert all(r.comprehend_label for r in saved.original.records)


def test_s3_claim_is_conditional_even_when_two_workers_read_the_same_etag(s3_bucket):
    s3, bucket = s3_bucket
    S3Repository(bucket, s3).save(DatasetBundle(dataset_id="race", original=make_dataset(["text"])))
    reads = Barrier(2)
    attempts = Barrier(2)

    class SynchronizedClient:
        exceptions = s3.exceptions

        def get_object(self, **kwargs):
            response = s3.get_object(**kwargs)
            reads.wait(timeout=10)
            return response

        def put_object(self, **kwargs):
            return s3.put_object(**kwargs)

    def compete():
        repo = S3Repository(bucket, SynchronizedClient())
        try:
            with repo.edit("race"):
                attempts.wait(timeout=10)  # Keep the winning claim until the loser attempted CAS.
                return "won"
        except ConflictError:
            attempts.wait(timeout=10)
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: compete(), range(2)))
    assert sorted(outcomes) == ["conflict", "won"]


def test_interrupted_s3_claim_fails_closed_and_does_not_expire(s3_bucket):
    import json

    s3, bucket = s3_bucket
    bundle = DatasetBundle(dataset_id="interrupted", original=make_dataset(["text"]))
    payload = bundle.model_dump(mode="json")
    payload["_write_claim"] = "worker-terminated-after-claim"
    s3.put_object(Bucket=bucket, Key="_work/interrupted.json", Body=json.dumps(payload))
    repo = S3Repository(bucket, s3)
    for _ in range(3):
        with pytest.raises(ConflictError), repo.edit("interrupted"):
            pytest.fail("An interrupted claim cannot be stolen and billed again")
    assert repo.get("interrupted").original.records[0].text == "text"


def test_local_reads_are_isolated_and_existing_writes_require_a_claim():
    repo = InMemoryRepository()
    bundle = DatasetBundle(dataset_id="d", original=make_dataset(["text"]))
    repo.save(bundle)
    snapshot = repo.get("d")
    snapshot.original.records[0].text = "changed without saving"
    assert repo.get("d").original.records[0].text == "text"
    with pytest.raises(ConflictError):
        repo.save(snapshot)
    with repo.edit("d") as edit:
        edit.save(snapshot)
    with pytest.raises(ConflictError):
        edit.save(bundle)
    assert repo.get("d").original.records[0].text == "changed without saving"


def test_local_checkpoint_writers_use_independent_temporary_files(tmp_path, monkeypatch):
    from pathlib import Path

    store = LocalCheckpointStore(tmp_path)
    replacing = Barrier(2)
    replace = Path.replace

    def synchronized_replace(path, target):
        replacing.wait(timeout=10)
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", synchronized_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda data: store.save("d", "collected", "csv", data), [b"first", b"second"])
        )
    assert len(results) == 2
    assert store.read("d", "collected", "csv") in (b"first", b"second")
    assert not list(tmp_path.rglob("*.tmp"))
