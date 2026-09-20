"""No provider calls are live: reload persisted progress after controlled failures/deadlines."""

from types import SimpleNamespace

import httpx
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError
from fastapi.testclient import TestClient

from sentiment_prep import budget
from sentiment_prep.api import deps
from sentiment_prep.api.app import create_app
from sentiment_prep.api.schemas import PreprocessRequest
from sentiment_prep.api.service import run_preprocessing
from sentiment_prep.config import Settings, get_settings
from sentiment_prep.labeling.service import label_with_comprehend
from sentiment_prep.models import DatasetBundle
from sentiment_prep.sources.spend_guard import InMemoryLedger, SpendGuard
from sentiment_prep.sources.x_search import XSearchSource
from sentiment_prep.storage.checkpoints import LocalCheckpointStore
from sentiment_prep.storage.local_repository import LocalRepository
from sentiment_prep.storage.repository import S3Repository
from tests.conftest import FakeBedrock, FakeComprehend, make_dataset


def test_local_journal_reloads_paid_results_and_excludes_independent_writers(tmp_path):
    from sentiment_prep.errors import ConflictError

    first, second = LocalRepository(tmp_path), LocalRepository(tmp_path)
    first.save(
        DatasetBundle(dataset_id="local", original=make_dataset([f"text {i}" for i in range(30)]))
    )
    provider = FakeComprehend()
    with first.edit("local") as edit:
        with pytest.raises(ConflictError), second.edit("local"):
            pytest.fail("Overlapping local edits must conflict")
        label_with_comprehend(
            edit.bundle, provider, Settings(_env_file=None), 25, persist=edit.save
        )
    replacement = LocalRepository(tmp_path)
    assert (
        sum(r.comprehend_label is not None for r in replacement.get("local").original.records) == 25
    )
    with replacement.edit("local") as edit:
        _, progress = label_with_comprehend(
            edit.bundle, provider, Settings(_env_file=None), 30, persist=edit.save
        )
    assert progress.done and progress.labelled_in_call == 5
    assert provider.calls == 2


@pytest.mark.parametrize(
    "failure",
    [
        ClientError({"Error": {"Code": "ThrottlingException"}}, "BatchDetectSentiment"),
        ClientError({"Error": {"Code": "InternalServerException"}}, "BatchDetectSentiment"),
        EndpointConnectionError(endpoint_url="https://offline.test"),
        ReadTimeoutError(endpoint_url="https://offline.test"),
    ],
)
def test_comprehend_batch_one_is_saved_before_batch_two_and_skipped_on_reload(s3_bucket, failure):
    s3, bucket = s3_bucket
    repo = S3Repository(bucket, s3)
    repo.save(
        DatasetBundle(
            dataset_id="labels", original=make_dataset([f"original {i}" for i in range(60)])
        )
    )
    calls = []

    class Provider(FakeComprehend):
        def batch_detect_sentiment(self, TextList, LanguageCode):
            calls.append(list(TextList))
            if len(calls) == 2:
                assert (
                    sum(r.comprehend_label is not None for r in repo.get("labels").original.records)
                    == 25
                )
                raise failure
            return super().batch_detect_sentiment(TextList, LanguageCode)

    provider = Provider()
    settings = Settings(_env_file=None)
    with repo.edit("labels") as edit:
        _, first = label_with_comprehend(edit.bundle, provider, settings, 60, persist=edit.save)
    assert first.partial and first.labelled_in_call == 25 and first.failed_in_call == 25
    restored = S3Repository(bucket, s3)
    with restored.edit("labels") as edit:
        _, second = label_with_comprehend(edit.bundle, provider, settings, 60, persist=edit.save)
    assert second.done and second.labelled_total == 60
    assert all(not (set(batch) & set(calls[0])) for batch in calls[1:])


@pytest.mark.parametrize("failure", ["transport", "service", "throttle"])
def test_x_saved_page_cursor_survives_failed_next_page_and_new_app(
    s3_bucket, tmp_path, monkeypatch, failure
):
    s3, bucket = s3_bucket

    def factory():
        return S3Repository(bucket, s3)

    tokens = []
    ledger = InMemoryLedger()
    now = [1000.0]
    monkeypatch.setattr(
        "sentiment_prep.sources.x_search.time",
        SimpleNamespace(
            time=lambda: now[0], sleep=lambda _: pytest.fail("long throttle must not sleep")
        ),
    )
    body = {"source": "x", "limit": 110, "query": "q", "request_id": "stable-request"}

    def posts(start, count):
        return [
            {
                "id": str(i),
                "text": f"unique post number {i} about this interesting subject",
                "lang": "en",
            }
            for i in range(start, start + count)
        ]

    def transport(request):
        token = request.url.params.get("next_token")
        tokens.append(token)
        if len(tokens) == 1:
            return httpx.Response(
                200, json={"data": posts(0, 100), "meta": {"next_token": "page-two"}}
            )
        if len(tokens) == 2:
            saved = factory().get("x-stable-request")
            assert len(saved.original.records) == 100 and saved.collection.next_token == "page-two"
            if failure == "transport":
                raise httpx.ReadTimeout("offline")
            return httpx.Response(
                503 if failure == "service" else 429, headers={"x-rate-limit-reset": "1060"}
            )
        return httpx.Response(200, json={"data": posts(100, 10), "meta": {}})

    def source(*args):
        return XSearchSource(
            SpendGuard(ledger, 1000, 3000, 0.005),
            httpx.Client(transport=httpx.MockTransport(transport), base_url="https://offline.test"),
        )

    monkeypatch.setattr(deps, "get_x_source", source)
    settings = Settings(_env_file=None, dedupe_enabled=False, api_key=None, api_key_ssm_path=None)

    def app_client():
        app = create_app()
        app.dependency_overrides[deps.get_repository] = factory
        app.dependency_overrides[deps.get_checkpoint_store] = lambda: LocalCheckpointStore(tmp_path)
        app.dependency_overrides[get_settings] = lambda: settings
        return TestClient(app)

    first = app_client().post("/dataset/load", json=body)
    assert first.status_code == 200
    assert first.json()["partial"] and first.json()["record_count"] == 100
    now[0] = 1061
    second = app_client().post("/dataset/load", json=body)
    assert second.status_code == 200
    assert not second.json()["partial"] and second.json()["record_count"] == 110
    assert tokens == [None, "page-two", "page-two"]
    assert app_client().post("/dataset/load", json=body).json()["record_count"] == 110
    assert len(tokens) == 3  # Repeating a completed request spends nothing.
    assert app_client().post("/dataset/load", json={**body, "query": "changed"}).status_code == 400


def test_slow_analysis_resumes_vectors_and_persists_cleaning_first(s3_bucket, monkeypatch):
    s3, bucket = s3_bucket
    repo = S3Repository(bucket, s3)
    repo.save(
        DatasetBundle(dataset_id="analysis", original=make_dataset(["LOUD TEXT", "OTHER TEXT"]))
    )
    now = [0.0]
    monkeypatch.setattr(budget, "time", SimpleNamespace(monotonic=lambda: now[0]))
    calls = []

    class SlowBedrock(FakeBedrock):
        def invoke_model(self, **kwargs):
            assert repo.get("analysis").processed is not None
            calls.append(kwargs["body"])
            now[0] += 6
            return super().invoke_model(**kwargs)

    request = PreprocessRequest(steps=["lowercase"], explain=False)
    provider = SlowBedrock()
    partial = True
    for _ in range(4):
        token = budget.current_budget.set(budget.RequestBudget(now[0] + 22))
        try:
            with S3Repository(bucket, s3).edit("analysis") as edit:
                saved, _, _ = run_preprocessing(
                    edit.bundle,
                    request,
                    Settings(_env_file=None),
                    None,
                    provider,
                    persist=edit.save,
                )
            partial = saved.analysis.partial
        finally:
            budget.current_budget.reset(token)
        if not partial:
            break
    assert not partial and saved.report.embedding_drift is not None
    assert len(calls) == len(set(calls)) == 4
    with repo.edit("analysis") as edit:
        run_preprocessing(
            edit.bundle, request, Settings(_env_file=None), None, provider, persist=edit.save
        )
    assert len(calls) == 4


def test_comprehend_deadline_stops_before_next_batch_and_resume_skips_success(
    s3_bucket, monkeypatch
):
    s3, bucket = s3_bucket
    repo = S3Repository(bucket, s3)
    repo.save(
        DatasetBundle(
            dataset_id="slow-labels", original=make_dataset([f"text {i}" for i in range(60)])
        )
    )
    now = [0.0]
    monkeypatch.setattr(budget, "time", SimpleNamespace(monotonic=lambda: now[0]))

    class SlowComprehend(FakeComprehend):
        def batch_detect_sentiment(self, **kwargs):
            now[0] += 11
            return super().batch_detect_sentiment(**kwargs)

    token = budget.current_budget.set(budget.RequestBudget(22))
    try:
        with repo.edit("slow-labels") as edit:
            _, progress = label_with_comprehend(
                edit.bundle, SlowComprehend(), Settings(_env_file=None), 60, persist=edit.save
            )
        assert progress.labelled_in_call == 25 and progress.stop_reason == "request_budget"
    finally:
        budget.current_budget.reset(token)
    with repo.edit("slow-labels") as edit:
        _, done = label_with_comprehend(
            edit.bundle, FakeComprehend(), Settings(_env_file=None), 60, persist=edit.save
        )
    assert done.labelled_in_call == 35 and done.done


def test_provider_success_before_failed_commit_is_explicitly_ambiguous(s3_bucket):
    """No external idempotency token exists: an uncommitted success may be billed again."""
    s3, bucket = s3_bucket
    repo = S3Repository(bucket, s3)
    repo.save(
        DatasetBundle(
            dataset_id="ambiguous", original=make_dataset([f"text {i}" for i in range(30)])
        )
    )
    provider = FakeComprehend()

    def failed_commit(bundle):
        raise OSError("storage unavailable after provider response")

    with repo.edit("ambiguous") as edit, pytest.raises(OSError):
        label_with_comprehend(
            edit.bundle, provider, Settings(_env_file=None), 30, persist=failed_commit
        )
    assert provider.calls == 1
    assert not any(r.comprehend_label for r in repo.get("ambiguous").original.records)
    with repo.edit("ambiguous") as edit:
        label_with_comprehend(
            edit.bundle, provider, Settings(_env_file=None), 25, persist=edit.save
        )
    assert provider.calls == 2  # Recovery cannot infer the lost provider response.


def test_explanation_failure_keeps_paid_embeddings_and_retries_only_explanation(s3_bucket):
    s3, bucket = s3_bucket
    repo = S3Repository(bucket, s3)
    repo.save(DatasetBundle(dataset_id="explain", original=make_dataset(["LOUD TEXT"])))

    class Provider(FakeBedrock):
        embeddings = 0
        explanations = 0

        def invoke_model(self, **kwargs):
            self.embeddings += 1
            return super().invoke_model(**kwargs)

        def converse(self, **kwargs):
            self.explanations += 1
            assert repo.get("explain").report.embedding_drift is not None
            if self.explanations == 1:
                raise ReadTimeoutError(endpoint_url="https://offline.test")
            return super().converse(**kwargs)

    provider = Provider()
    request = PreprocessRequest(steps=["lowercase"])
    for _ in range(2):
        with S3Repository(bucket, s3).edit("explain") as edit:
            saved, _, _ = run_preprocessing(
                edit.bundle, request, Settings(_env_file=None), None, provider, persist=edit.save
            )
    assert not saved.analysis.partial and saved.report.explanation
    assert provider.embeddings == 2 and provider.explanations == 2


def test_analysis_comprehend_cache_survives_later_batch_failure(s3_bucket):
    s3, bucket = s3_bucket
    repo = S3Repository(bucket, s3)
    repo.save(
        DatasetBundle(dataset_id="compare", original=make_dataset([f"text {i}" for i in range(60)]))
    )
    batches = []

    class Provider(FakeComprehend):
        def batch_detect_sentiment(self, TextList, LanguageCode):
            batches.append(list(TextList))
            if len(batches) == 2:
                assert len(repo.get("compare").analysis.sentiment) == 25
                raise EndpointConnectionError(endpoint_url="https://offline.test")
            return super().batch_detect_sentiment(TextList, LanguageCode)

    provider = Provider()
    request = PreprocessRequest(steps=["lowercase"], explain=False)
    with repo.edit("compare") as edit:
        saved, _, _ = run_preprocessing(
            edit.bundle, request, Settings(_env_file=None), provider, None, persist=edit.save
        )
    assert saved.analysis.partial
    with S3Repository(bucket, s3).edit("compare") as edit:
        saved, _, _ = run_preprocessing(
            edit.bundle, request, Settings(_env_file=None), provider, None, persist=edit.save
        )
    assert not saved.analysis.partial and saved.report.sentiment.comparable_records == 60
    assert all(not (set(batch) & set(batches[0])) for batch in batches[1:])
