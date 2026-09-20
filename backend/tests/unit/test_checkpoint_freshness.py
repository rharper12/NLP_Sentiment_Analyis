"""Stored artifacts remain usable but never masquerade as fresh after source changes/failures."""

import httpx
import pytest
from fastapi.testclient import TestClient

from sentiment_prep.api import deps, routes
from sentiment_prep.api.app import create_app
from sentiment_prep.api.routes import convert_checkpoint, list_checkpoints, preprocess
from sentiment_prep.api.schemas import LoadRequest, PreprocessRequest
from sentiment_prep.config import Settings, get_settings
from sentiment_prep.errors import AppError, ConflictError
from sentiment_prep.labeling.service import ManualLabel, apply_manual_labels, summary
from sentiment_prep.models import DatasetBundle
from sentiment_prep.sources.spend_guard import InMemoryLedger, SpendGuard
from sentiment_prep.sources.x_search import XSearchSource
from sentiment_prep.storage.checkpoints import (
    LocalCheckpointStore,
    checkpoint_bundle,
    checkpoint_warnings,
)
from sentiment_prep.storage.local_repository import LocalRepository
from sentiment_prep.storage.repository import InMemoryRepository
from tests.conftest import make_dataset


def test_partial_local_write_keeps_previous_snapshot_and_removes_temp(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from sentiment_prep.storage import checkpoints

    store = LocalCheckpointStore(tmp_path)
    store.save("d", "collected", "csv", b"previous")
    original = checkpoints.NamedTemporaryFile

    @contextmanager
    def fail_during_write(**kwargs):
        with original(**kwargs) as temporary:

            class BrokenWriter:
                name = temporary.name

                def write(self, data):
                    temporary.write(data[:2])
                    raise OSError("disk full")

            yield BrokenWriter()

    monkeypatch.setattr(checkpoints, "NamedTemporaryFile", fail_during_write)
    with pytest.raises(OSError, match="disk full"):
        store.save("d", "collected", "csv", b"replacement")
    assert store.read("d", "collected", "csv") == b"previous"
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("existing", [False, True])
def test_checkpoint_failure_preserves_prior_snapshot_and_safe_warning(
    tmp_path, monkeypatch, existing
):
    store = LocalCheckpointStore(tmp_path)
    bundle = DatasetBundle(dataset_id="snapshot", original=make_dataset(["Original"]))
    if existing:
        bundle = checkpoint_bundle(store, bundle, "collected")
    before = store.read("snapshot", "collected", "csv")
    bundle.original.records[0].text = "Replacement"

    def fail(*args):
        raise OSError("/protected/path")

    monkeypatch.setattr(store, "save", fail)
    failed = checkpoint_bundle(store, bundle, "collected")
    assert failed.checkpoint_status["collected:csv"].status == "failed"
    assert store.read("snapshot", "collected", "csv") == before
    assert "could not be updated" in checkpoint_warnings(failed)[0]
    assert "/protected/path" not in str(checkpoint_warnings(failed))
    from sentiment_prep.api.routes import _summary

    assert checkpoint_warnings(failed)[0] in _summary(failed).warnings


def test_converted_revision_becomes_stale_and_regenerates(tmp_path, monkeypatch):
    from sentiment_prep.api import deps

    monkeypatch.setattr(deps, "checkpoint_location", lambda: "local")
    repo, store = InMemoryRepository(), LocalCheckpointStore(tmp_path)
    bundle = checkpoint_bundle(
        store, DatasetBundle(dataset_id="d", original=make_dataset(["first"])), "collected"
    )
    repo.save(bundle)
    first = convert_checkpoint("d", "collected", repo, store)
    assert first.status == "current"
    prior = store.read("d", "collected", "parquet")
    with repo.edit("d") as edit:
        changed = edit.bundle
        changed.original.records[0].text = "second"
        edit.save(checkpoint_bundle(store, changed, "collected"))
    files = list_checkpoints("d", repo, store).items
    assert {f.format: f.status for f in files} == {"csv": "current", "parquet": "stale"}
    assert store.read("d", "collected", "parquet") == prior
    fresh = convert_checkpoint("d", "collected", repo, store)
    assert fresh.status == "current" and fresh.revision != first.revision
    assert store.read("d", "collected", "parquet") != prior


def test_preprocessing_rerun_marks_labelled_snapshot_stale_and_public_status_is_safe(
    tmp_path, monkeypatch
):
    from sentiment_prep.api import deps
    from sentiment_prep.api.service import run_preprocessing
    from sentiment_prep.report import render_report

    repo, store = InMemoryRepository(), LocalCheckpointStore(tmp_path)
    settings = Settings(
        _env_file=None, diagnostics=False, comprehend_enabled=False, bedrock_enabled=False
    )
    bundle = DatasetBundle(dataset_id="d", original=make_dataset(["UPPER TEXT"]))
    bundle, _, _ = run_preprocessing(
        bundle, PreprocessRequest(steps=["lowercase"], explain=False), settings, None, None
    )
    bundle = apply_manual_labels(bundle, [ManualLabel(id="r0", label="positive")])
    bundle = checkpoint_bundle(store, bundle, "labelled")
    old = store.read("d", "labelled", "csv")
    repo.save(bundle)
    monkeypatch.setattr(deps, "get_comprehend_client", lambda: None)
    monkeypatch.setattr(deps, "get_bedrock_client", lambda: None)
    response = preprocess(
        "d", PreprocessRequest(steps=["tokenize"], explain=False), repo, store, settings
    )
    assert b"labelled checkpoint is outdated" in response.body
    assert str(tmp_path).encode() not in response.body
    assert store.read("d", "labelled", "csv") == old
    result = repo.get("d")
    assert summary(result).warnings
    report = render_report(result, diagnostics=False)
    assert "checkpoint is outdated" in report and str(tmp_path) not in report
    refreshed = checkpoint_bundle(store, result, "labelled")
    assert not checkpoint_warnings(refreshed)


def test_failed_conversion_preserves_prior_bytes_and_marks_failure(tmp_path, monkeypatch):
    repo, store = InMemoryRepository(), LocalCheckpointStore(tmp_path)
    repo.save(
        checkpoint_bundle(
            store, DatasetBundle(dataset_id="d", original=make_dataset(["first"])), "collected"
        )
    )
    convert_checkpoint("d", "collected", repo, store)
    before = store.read("d", "collected", "parquet")
    monkeypatch.setattr(
        "sentiment_prep.api.routes.csv_to_parquet",
        lambda data: (_ for _ in ()).throw(ValueError("failed")),
    )
    with pytest.raises(AppError, match="conversion failed"):
        convert_checkpoint("d", "collected", repo, store)
    assert repo.get("d").checkpoint_status["collected:parquet"].status == "failed"
    assert store.read("d", "collected", "parquet") == before


@pytest.mark.parametrize("change", ["append", "deduplicate", "overlap"])
def test_collection_resume_tracks_downstream_freshness_and_preserves_labels(
    tmp_path, monkeypatch, change
):
    """Source changes invalidate descendants; cursor-only resumes keep them current."""
    settings = Settings(
        _env_file=None,
        diagnostics=False,
        api_key=None,
        api_key_ssm_path=None,
        dedupe_enabled=change == "deduplicate",
        dedupe_similarity=1.0,
        comprehend_enabled=False,
        bedrock_enabled=False,
    )
    calls = []

    def page(request):
        offset = len(calls) * 100
        calls.append(request)
        start = offset if offset < 500 or change == "append" else 0
        posts = [
            {"id": str(i), "text": f"Post {i} has enough useful words", "lang": "en"}
            for i in range(start, start + 100)
        ]
        if change == "deduplicate" and offset == 400:
            posts[-1]["text"] = "Post 0 has enough useful words"
        return httpx.Response(
            200,
            json={"data": posts, "meta": {"next_token": str(offset + 100)} if offset < 500 else {}},
        )

    repo = LocalRepository(tmp_path / "working")
    store = LocalCheckpointStore(tmp_path / "protected-checkpoints")
    guard = SpendGuard(InMemoryLedger(), 1000, 3000, 0.005)
    monkeypatch.setattr(deps, "get_comprehend_client", lambda: None)
    monkeypatch.setattr(deps, "get_bedrock_client", lambda: None)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[deps.get_repository] = lambda: repo
    app.dependency_overrides[deps.get_checkpoint_store] = lambda: store
    request = LoadRequest(source="x", query="freshness", limit=600, request_id="freshness")
    with (
        httpx.Client(
            transport=httpx.MockTransport(page), base_url="https://offline.test"
        ) as provider,
        TestClient(app) as client,
    ):
        monkeypatch.setattr(deps, "get_x_source", lambda *a: XSearchSource(guard, provider))
        first = routes._collect_x(request, repo, store, settings, lambda: len(calls) >= 5)
        assert first.record_count == 500 and first.partial
        root = f"/dataset/{first.dataset_id}"
        preprocessing = {"steps": ["lowercase"], "explain": False}
        assert client.post(f"{root}/preprocess", json=preprocessing).status_code == 200
        labels = {"items": [{"id": "0", "label": "positive"}]}
        assert client.post(f"{root}/labels/manual", json=labels).status_code == 200
        for stage in ("processed", "labelled"):
            assert convert_checkpoint(first.dataset_id, stage, repo, store).status == "current"
        # Refresh the collected snapshot after the label edit, isolating source resume changes.
        with repo.edit(first.dataset_id) as edit:
            edit.save(checkpoint_bundle(store, edit.bundle, "collected"))
        before = repo.get(first.dataset_id)
        prior = {
            (stage, fmt): store.read(first.dataset_id, stage, fmt)
            for stage in ("processed", "labelled")
            for fmt in ("csv", "parquet")
        }

        # Cancellation changes only collection metadata: no downstream invalidation or spending.
        routes._collect_x(request, repo, store, settings, lambda: True)
        unchanged = repo.get(first.dataset_id)
        assert unchanged.checkpoint_status == before.checkpoint_status
        assert len(calls) == 5

        resumed = routes._collect_x(request, repo, store, settings, lambda: False)
        # Reopen the repository so assertions cover persisted, revalidated state.
        result = LocalRepository(tmp_path / "working").get(first.dataset_id)
        expected_rows = {"append": 600, "deduplicate": 499, "overlap": 500}[change]
        assert resumed.record_count == len(result.original.records) == expected_rows
        assert resumed.resume_request_id == request.request_id and not resumed.partial
        source_before = before.checkpoint_status["collected:csv"].revision
        source_after = result.checkpoint_status["collected:csv"].revision
        assert (source_before != source_after) is (change != "overlap")
        assert result.original.records[0].label == "positive"
        assert result.original.records[0].label_source == "manual"
        if change == "append":
            assert {r.id for r in result.original.records} == {str(i) for i in range(600)}
        assert len(result.processed.records) == 500
        expected_status = "current" if change == "overlap" else "stale"
        listed = list_checkpoints(first.dataset_id, repo, store)
        for (stage, fmt), content in prior.items():
            key = f"{stage}:{fmt}"
            assert result.checkpoint_status[key].status == expected_status
            assert result.checkpoint_status[key].revision == before.checkpoint_status[key].revision
            assert store.read(first.dataset_id, stage, fmt) == content
            assert (
                next(i for i in listed.items if (i.stage, i.format) == (stage, fmt)).status
                == expected_status
            )

        if change == "overlap":
            assert not checkpoint_warnings(result)
            return

        assert any("rerun preprocessing" in warning for warning in resumed.warnings)
        assert "protected-checkpoints" not in resumed.model_dump_json()
        assert client.get(f"{root}/checkpoints").status_code == 404
        assert client.post(f"{root}/checkpoints/processed/parquet").status_code == 404
        for endpoint in (root, f"{root}/labels/summary", f"{root}/report.md"):
            response = client.get(endpoint)
            assert response.status_code == 200
            assert "rerun preprocessing" in response.text
            assert str(tmp_path) not in response.text
            assert "protected-checkpoints" not in response.text
        for stage in ("processed", "labelled"):
            with pytest.raises(ConflictError, match="Regenerate the outdated CSV"):
                convert_checkpoint(first.dataset_id, stage, repo, store)

        # A label edit alone cannot make the old processed input fresh.
        assert client.post(f"{root}/labels/manual", json=labels).status_code == 200
        assert repo.get(first.dataset_id).checkpoint_status["labelled:csv"].status == "stale"
        response = client.post(f"{root}/preprocess", json=preprocessing)
        assert response.status_code == 200 and response.json()["record_count"] == expected_rows
        refreshed = repo.get(first.dataset_id)
        assert refreshed.checkpoint_status["processed:csv"].status == "current"
        assert (
            refreshed.checkpoint_status["processed:csv"].revision
            != before.checkpoint_status["processed:csv"].revision
        )
        for key in ("processed:parquet", "labelled:csv", "labelled:parquet"):
            assert refreshed.checkpoint_status[key].status == "stale"
        assert refreshed.original.records[0].label == "positive"
        assert client.post(f"{root}/labels/manual", json=labels).status_code == 200
        assert repo.get(first.dataset_id).checkpoint_status["labelled:csv"].status == "current"
        assert repo.get(first.dataset_id).checkpoint_status["labelled:parquet"].status == "stale"
        for stage in ("processed", "labelled"):
            assert convert_checkpoint(first.dataset_id, stage, repo, store).status == "current"
