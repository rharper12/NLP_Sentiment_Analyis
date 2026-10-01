"""Local SSO recovery must preserve work, diagnose accurately, and avoid shell interpolation."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from botocore.exceptions import (
    ClientError,
    SSOTokenLoadError,
    TokenRetrievalError,
    UnauthorizedSSOTokenError,
)

from sentiment_prep.api import deps
from sentiment_prep.api.routes import label_comprehend, preprocess
from sentiment_prep.api.schemas import ComprehendLabelRequest, PreprocessRequest
from sentiment_prep.api.service import run_preprocessing
from sentiment_prep.aws import is_sso_session_error
from sentiment_prep.config import Settings, get_settings
from sentiment_prep.errors import CredentialsError
from sentiment_prep.models import DatasetBundle
from sentiment_prep.storage.checkpoints import (
    LocalCheckpointStore,
    checkpoint_bundle,
    checkpoint_warnings,
)
from sentiment_prep.storage.repository import InMemoryRepository
from tests.conftest import FakeComprehend, make_dataset

spec = importlib.util.spec_from_file_location(
    "aws_check", Path(__file__).resolve().parents[3] / "tools" / "aws_check.py"
)
aws_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aws_check)


def expired():
    return TokenRetrievalError(provider="sso", error_msg="private provider details")


@pytest.mark.parametrize(
    "error,expected",
    [
        (expired(), True),
        (SSOTokenLoadError(error_msg="private cache path"), True),
        (UnauthorizedSSOTokenError(), True),
        (TokenRetrievalError(provider="other", error_msg="expired"), False),
        (ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject"), False),
        (ClientError({"Error": {"Code": "ThrottlingException"}}, "BatchDetectSentiment"), False),
        (OSError("disk full"), False),
    ],
)
def test_only_sso_failures_are_diagnosed_as_sso(error, expected):
    assert is_sso_session_error(error) is expected


def test_expired_sso_pauses_without_exhausting_documents_and_recovers_saved_successes():
    class Provider(FakeComprehend):
        def __init__(self):
            super().__init__()
            self.blocked = True
            self.submitted = []

        def batch_detect_sentiment(self, TextList, LanguageCode):
            if self.calls and self.blocked:
                self.calls += 1
                raise expired()
            self.submitted.extend(TextList)
            return super().batch_detect_sentiment(TextList, LanguageCode)

    provider = Provider()
    settings = Settings(comprehend_enabled=True, diagnostics=True)
    request = PreprocessRequest(steps=[])
    bundle = DatasetBundle(
        dataset_id="sso", original=make_dataset([f"text {i}" for i in range(26)])
    )
    committed = []
    for attempt in range(4):
        bundle, _, _ = run_preprocessing(
            bundle,
            request,
            settings,
            provider,
            persist=lambda saved: committed.append(saved.model_copy(deep=True)),
        )
        bundle = DatasetBundle.model_validate_json(bundle.model_dump_json())
        assert bundle.analysis.partial
        assert "make aws-login" in " ".join(bundle.report.warnings)
        assert not bundle.analysis.attempts
        assert len(bundle.analysis.sentiment) == 25
        assert all(result.label != "error" for result in bundle.analysis.sentiment.values())
        assert provider.calls == attempt + 2  # One blocked call per explicit resume.
    assert len(committed[-1].analysis.sentiment) == 25
    provider.blocked = False
    bundle, _, _ = run_preprocessing(bundle, request, settings, provider)
    assert not bundle.analysis.partial and bundle.analysis.completed == ["sentiment"]
    assert len(provider.submitted) == len(set(provider.submitted)) == 26
    assert not any(
        "SSO" in warning or "make aws-login" in warning for warning in bundle.report.warnings
    )


@pytest.mark.parametrize(
    "runtime,diagnostics,has_hint",
    [
        ("local", True, True),
        ("local", False, False),
        ("lambda", True, False),
        ("lambda", False, False),
    ],
)
def test_analysis_and_checkpoint_response_gate_local_recovery_commands(
    tmp_path, monkeypatch, runtime, diagnostics, has_hint
):
    def fail(*args, **kwargs):
        raise expired()

    store = LocalCheckpointStore(tmp_path)
    monkeypatch.setattr(store, "save", fail)
    bundle = checkpoint_bundle(
        store, DatasetBundle(dataset_id="sso", original=make_dataset(["one text"])), "collected"
    )
    repo = InMemoryRepository()
    repo.save(bundle)
    monkeypatch.setattr(deps, "get_comprehend_client", fail)
    settings = Settings(runtime=runtime, diagnostics=diagnostics, comprehend_enabled=True)
    response = preprocess("sso", PreprocessRequest(steps=[]), repo, store, settings)
    payload = response.model_dump() if diagnostics else json.loads(response.body)
    assert payload["partial"]
    for warnings in (payload["warnings"], payload["report"]["warnings"]):
        assert ("make aws-login" in " ".join(warnings)) is has_hint
        assert "private provider details" not in " ".join(warnings)
    assert len(repo.get("sso").original.records) == 1


def test_checkpoint_failure_cause_round_trips_and_clears_after_recovery(tmp_path, monkeypatch):
    store = LocalCheckpointStore(tmp_path)
    bundle = DatasetBundle(dataset_id="sso", original=make_dataset(["original"]))
    bundle = checkpoint_bundle(store, bundle, "collected")
    previous = store.read("sso", "collected", "csv")

    def fail(*args):
        raise expired()

    with monkeypatch.context() as patch:
        patch.setattr(store, "save", fail)
        bundle = checkpoint_bundle(store, bundle, "collected")
    bundle = DatasetBundle.model_validate_json(bundle.model_dump_json())
    assert bundle.checkpoint_status["collected:csv"].failure_code == "sso_session_unavailable"
    assert "make aws-login" in checkpoint_warnings(bundle, local_dev=True)[0]
    assert "make aws-login" not in checkpoint_warnings(bundle)[0]
    assert store.read("sso", "collected", "csv") == previous
    bundle = checkpoint_bundle(store, bundle, "collected")
    assert bundle.checkpoint_status["collected:csv"].failure_code is None
    assert checkpoint_warnings(bundle, local_dev=True) == []


@pytest.mark.parametrize("runtime", ["local", "lambda"])
def test_labeling_sso_error_keeps_committed_labels_and_can_resume(tmp_path, monkeypatch, runtime):
    class Provider(FakeComprehend):
        def batch_detect_sentiment(self, TextList, LanguageCode):
            if self.calls == 1:
                self.calls += 1
                raise expired()
            return super().batch_detect_sentiment(TextList, LanguageCode)

    provider = Provider()
    monkeypatch.setattr(deps, "get_comprehend_client", lambda: provider)
    monkeypatch.setattr(deps, "get_comprehend_rate", lambda: None)
    repo = InMemoryRepository()
    repo.save(
        DatasetBundle(dataset_id="sso", original=make_dataset([f"text {i}" for i in range(26)]))
    )
    store = LocalCheckpointStore(tmp_path)
    settings = Settings(runtime=runtime, diagnostics=True)
    request = ComprehendLabelRequest(confirm_cost=True, max_records=26)
    with pytest.raises(CredentialsError) as caught:
        label_comprehend("sso", request, repo, store, settings)
    assert caught.value.status_code == 503
    assert ("make aws-login" in caught.value.message) is (runtime == "local")
    assert "private provider details" not in caught.value.message
    saved = repo.get("sso")
    assert sum(record.comprehend_label is not None for record in saved.original.records) == 25
    assert not saved.label_failures
    resumed = label_comprehend("sso", request, repo, store, settings)
    assert resumed.done and resumed.labelled_in_call == 1 and resumed.labelled_total == 26


@pytest.mark.parametrize("environment_profile", [None, "environment-profile"])
def test_login_command_reads_app_dotenv_and_environment_without_probing(
    tmp_path, monkeypatch, environment_profile
):
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / ".env").write_text("AWS_PROFILE=dotenv-profile\nCOMPREHEND_ENABLED=true\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(aws_check, "BACKEND", backend)
    monkeypatch.setattr(aws_check.sys, "argv", ["aws_check.py", "--login"])
    monkeypatch.setitem(Settings.model_config, "env_file", ".env")
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE"):
        monkeypatch.delenv(key, raising=False)
    if environment_profile:
        monkeypatch.setenv("AWS_PROFILE", environment_profile)
    monkeypatch.setenv("PROBE", "1")
    monkeypatch.setenv("COMPREHEND_ENABLED", "true")
    monkeypatch.setattr(deps, "boto_session", lambda: pytest.fail("unexpected AWS SDK call"))
    monkeypatch.setattr(deps, "get_comprehend_client", lambda: pytest.fail("unexpected probe"))
    monkeypatch.setattr(aws_check.shutil, "which", lambda _: "/fake/aws")
    calls = []

    def run(arguments, **kwargs):
        calls.append(arguments)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(aws_check.subprocess, "run", run)
    get_settings.cache_clear()
    try:
        assert aws_check.main() == 0
    finally:
        get_settings.cache_clear()
    assert calls == [
        ["/fake/aws", "sso", "login", "--profile", environment_profile or "dotenv-profile"]
    ]


@pytest.mark.parametrize("status", [0, 7])
def test_login_passes_profile_as_one_argument_and_propagates_cli_status(monkeypatch, status):
    calls = []
    monkeypatch.setattr(aws_check.shutil, "which", lambda _: "/fake/aws")

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(returncode=status)

    monkeypatch.setattr(aws_check.subprocess, "run", run)
    settings = Settings(
        aws_profile="profile with spaces; $(touch forbidden)",
        aws_access_key_id=None,
        aws_secret_access_key=None,
    )
    assert aws_check.login(settings) == status
    assert calls == [
        (["/fake/aws", "sso", "login", "--profile", settings.aws_profile], {"check": False})
    ]


@pytest.mark.parametrize(
    "profile,static,cli", [(None, False, True), ("PROD", True, True), ("PROD", False, False)]
)
def test_login_explains_configuration_problems_without_starting_a_process(
    monkeypatch, capsys, profile, static, cli
):
    monkeypatch.setattr(aws_check.shutil, "which", lambda _: "/fake/aws" if cli else None)
    monkeypatch.setattr(aws_check.subprocess, "run", lambda *a, **kw: pytest.fail("unexpected CLI"))
    settings = Settings(
        aws_profile=profile,
        aws_access_key_id="key" if static else None,
        aws_secret_access_key="secret" if static else None,
    )
    assert aws_check.login(settings) == 1
    assert (
        "static access keys" if static else "AWS_PROFILE" if not profile else "AWS CLI v2"
    ) in capsys.readouterr().out
