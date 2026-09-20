"""Unicode preparation and untrusted provider payload regression coverage."""

import json
import math
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest

from sentiment_prep.analysis.comprehend_scorer import ComprehendScorer, billable_units
from sentiment_prep.analysis.comprehend_text import prepare_text
from sentiment_prep.analysis.embeddings import cosine
from sentiment_prep.analysis.payloads import ConverseResponse
from sentiment_prep.api.schemas import PreprocessRequest
from sentiment_prep.api.service import run_preprocessing
from sentiment_prep.config import Settings
from sentiment_prep.errors import ConfigurationError, ExternalServiceError
from sentiment_prep.labeling.service import estimate, label_with_comprehend
from sentiment_prep.models import DatasetBundle
from sentiment_prep.sources.huggingface import HuggingFaceSource
from sentiment_prep.sources.x_search import XSearchSource
from tests.conftest import FakeBedrock, FakeComprehend, make_dataset
from tests.unit.test_sources import guard, ledger_of, post, x_client


@pytest.mark.parametrize(
    "text,chars,truncated",
    [
        ("a" * 301, 301, False),
        ("é" * 301, 301, False),
        ("😀" * 301, 301, False),
        ("aé字😀" * 500, 2000, False),
        ("a" * 5000, 5000, False),
        ("é" * 2500, 2500, False),
        ("😀" * 1250, 1250, False),
        ("é" * 2501, 2500, True),
        ("😀" * 1251, 1250, True),
        ("a" * 4999 + "😀", 4999, True),
        ("字" * 10000, 1666, True),
        ("", 1, False),
    ],
)
def test_unicode_request_and_billing_share_preparation(text, chars, truncated):
    prepared = prepare_text(text)
    assert prepared.original_chars == len(text)
    assert prepared.submitted_chars == chars
    assert prepared.submitted_bytes == len(prepared.text.encode()) <= 5000
    assert prepared.text.encode().decode() == prepared.text
    assert prepared.truncated is truncated
    assert billable_units(text, 100, 3) == max(3, math.ceil(chars / 100))

    class Capture(FakeComprehend):
        def batch_detect_sentiment(self, **kwargs):
            self.submitted = kwargs["TextList"]
            return super().batch_detect_sentiment(**kwargs)

    client = Capture()
    bundle = DatasetBundle(dataset_id="unicode", original=make_dataset([text]))
    quoted = estimate(bundle, Settings(_env_file=None), None)
    saved, progress = label_with_comprehend(bundle, client, Settings(_env_file=None), 25)
    assert client.submitted == [prepared.text]
    assert quoted.billable_units == progress.units_billed == max(3, math.ceil(chars / 100))
    assert quoted.truncated_records == progress.truncated_records == int(truncated)
    assert saved.original.records[0].text == text


def test_billing_counts_actual_deduplicated_prefixes_per_batch():
    texts = ["é" * 2500 + str(i) for i in range(26)]
    bundle = DatasetBundle(dataset_id="prefixes", original=make_dataset(texts))
    quoted = estimate(bundle, Settings(_env_file=None), None)
    _, progress = label_with_comprehend(bundle, FakeComprehend(), Settings(_env_file=None), 100)
    assert quoted.billable_units == progress.units_billed == 50


def test_analysis_discloses_prefix_sentiment():
    bundle = DatasetBundle(dataset_id="prefix", original=make_dataset(["É" * 3000]))
    saved, _, _ = run_preprocessing(
        bundle,
        PreprocessRequest(steps=["lowercase"], explain=False),
        Settings(_env_file=None),
        FakeComprehend(),
        None,
    )
    assert any("5,000 UTF-8 bytes" in warning for warning in saved.report.warnings)


def response():
    return FakeComprehend().batch_detect_sentiment(["love this", "not good"], "en")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(ResultList=None),
        lambda p: p.pop("ErrorList"),
        lambda p: p["ResultList"][0].update(Index=-1),
        lambda p: p["ResultList"][0].update(Index=2),
        lambda p: p["ResultList"][0].update(Index=True),
        lambda p: p["ResultList"][0].update(Index=0.0),
        lambda p: p["ResultList"][0].update(Index="0"),
        lambda p: p["ResultList"][0].update(Sentiment="HAPPY"),
        lambda p: p["ResultList"][0].update(SentimentScore={"Positive": 0.9}),
        lambda p: p["ResultList"][0]["SentimentScore"].update(Positive=float("nan")),
        lambda p: p["ResultList"][0]["SentimentScore"].update(Positive=float("inf")),
        lambda p: p["ResultList"][0]["SentimentScore"].update(Positive=-0.1),
        lambda p: p["ResultList"][0]["SentimentScore"].update(Positive=1.1),
        lambda p: p["ResultList"][0]["SentimentScore"].update(Positive="0.9"),
        lambda p: p["ResultList"][0]["SentimentScore"].update(Positive=True),
        lambda p: p["ResultList"][1].update(Index=0),
        lambda p: p["ErrorList"].append({"Index": 0, "ErrorCode": "INVALID_TEXT"}),
        lambda p: p["ErrorList"].append({"Index": 0, "ErrorCode": None}),
    ],
)
def test_malformed_sentiment_never_assigns_any_label(mutation):
    payload = response()
    mutation(payload)
    client = SimpleNamespace(batch_detect_sentiment=lambda **kwargs: payload)
    labels = ComprehendScorer(client).label_texts(["first", "second"])
    assert all(label.label == "error" and label.confidence is None for label in labels)


def test_missing_sentiment_result_is_explicit_and_indices_can_arrive_out_of_order():
    payload = response()
    payload["ResultList"].reverse()
    client = SimpleNamespace(batch_detect_sentiment=lambda **kwargs: payload)
    assert [r.label for r in ComprehendScorer(client).label_texts(["a", "b"])] == [
        "positive",
        "negative",
    ]
    payload["ResultList"].pop()
    labels = ComprehendScorer(client).label_texts(["a", "b"])
    assert labels[0].error_code == "missing_result" and labels[1].label == "negative"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"embedding": []},
        {"embedding": [0.0] * 26},
        {"embedding": "bad"},
        *[
            {"embedding": [bad] + [0.0] * 1023}
            for bad in (None, "1", True, float("nan"), float("inf"), -float("inf"))
        ],
    ],
)
def test_bad_titan_payload_degrades_without_persisting_vectors_or_metrics(payload):
    client = SimpleNamespace(
        invoke_model=lambda **kwargs: {"body": BytesIO(json.dumps(payload).encode())}
    )
    bundle = DatasetBundle(dataset_id="titan", original=make_dataset(["LOUD TEXT"]))
    saved, before, after = run_preprocessing(
        bundle,
        PreprocessRequest(steps=["lowercase"], explain=False),
        Settings(_env_file=None),
        None,
        client,
    )
    assert saved.processed.records[0].text == "loud text"
    assert before.record_count == after.record_count == 1
    assert saved.report.embedding_drift is None and not saved.analysis.vectors
    assert any("embedding unavailable" in w for w in saved.report.warnings)
    assert "NaN" not in saved.model_dump_json()


def test_large_finite_embedding_values_cannot_overflow_cosine():
    assert cosine([1e308] * 1024, [1e308] * 1024) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"output": {}},
        {"output": {"message": {}}},
        *[
            {"output": {"message": {"content": content}}}
            for content in (
                None,
                [],
                [None],
                [{"text": None}],
                [{"text": 12}],
                [{"text": "  "}],
                [{"toolUse": {}}],
            )
        ],
    ],
)
def test_bad_converse_degrades(payload):
    class Provider(FakeBedrock):
        def converse(self, **kwargs):
            return payload

    bundle = DatasetBundle(dataset_id="converse", original=make_dataset(["LOUD"]))
    saved, _, _ = run_preprocessing(
        bundle, PreprocessRequest(steps=["lowercase"]), Settings(_env_file=None), None, Provider()
    )
    assert saved.report.embedding_drift is not None
    assert saved.report.explanation is None
    assert any("explanation unavailable" in w for w in saved.report.warnings)


def test_converse_finds_text_after_non_text_block():
    assert (
        ConverseResponse.model_validate(
            {
                "output": {
                    "message": {
                        "content": [{"toolUse": {}}, {"text": " Valid "}, {"text": "second"}]
                    }
                }
            }
        ).text()
        == "Valid\nsecond"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"Parameter": None},
        {"Parameter": {}},
        *[
            {"Parameter": {"Value": value}}
            for value in (None, "", "  ", 7, {"secret": "never-leak-this-value"})
        ],
    ],
)
def test_ssm_rejects_malformed_values_without_exposing_secrets(monkeypatch, caplog, payload):
    session = SimpleNamespace(
        client=lambda *args, **kwargs: SimpleNamespace(get_parameter=lambda **kwargs: payload)
    )
    monkeypatch.setattr("sentiment_prep.config.create_aws_session", lambda settings: session)
    with pytest.raises(ConfigurationError) as caught:
        Settings(_env_file=None, api_key=None, api_key_ssm_path="/secret").resolve_api_key()
    assert "never-leak-this-value" not in str(caught.value) + caplog.text


def test_ssm_sdk_exception_is_not_logged_or_chained(monkeypatch, caplog):
    def fail(settings):
        raise RuntimeError("never-leak-this-value")

    monkeypatch.setattr("sentiment_prep.config.create_aws_session", fail)
    with pytest.raises(ConfigurationError) as caught:
        Settings(_env_file=None, api_key=None, api_key_ssm_path="/secret").resolve_api_key()
    import traceback

    assert (
        "never-leak-this-value"
        not in "".join(traceback.format_exception(caught.value)) + caplog.text
    )


@pytest.mark.parametrize(
    "change",
    [
        {"text": None},
        {"text": 5},
        {"id": None},
        {"id": 1},
        {"id": " "},
        {"created_at": "wrong"},
        {"lang": []},
    ],
)
def test_invalid_x_records_are_not_persisted_and_keep_spend(change):
    g = guard()
    source = XSearchSource(
        g, x_client([httpx.Response(200, json={"data": [{**post(1), **change}], "meta": {}})])
    )
    with pytest.raises(ExternalServiceError, match="malformed"):
        source.fetch(10, query="q")
    assert ledger_of(g).reserved(g.today()) == 10


def test_invalid_x_later_page_keeps_prior_records_and_cursor():
    g = guard()
    saves = []
    source = XSearchSource(
        g,
        x_client(
            [
                httpx.Response(200, json={"data": [post(1)], "meta": {"next_token": "second"}}),
                httpx.Response(200, json={"data": [{"id": "2", "text": None}], "meta": {}}),
            ]
        ),
    )
    dataset = source.fetch(
        20, query="q", persist=lambda ds, state: saves.append((deepcopy(ds), deepcopy(state)))
    )
    assert [r.id for r in dataset.records] == ["1"]
    assert saves[-1][1].next_token == "second" and saves[-1][1].reads == 20


def hf(payload):
    return HuggingFaceSource(
        "tweet_eval",
        "sentiment",
        "train",
        "text",
        "label",
        httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
            base_url="https://offline.test",
        ),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"rows": None},
        {"rows": [None]},
        *[
            {"rows": [{"row_idx": idx, "row": {"text": "valid"}}]}
            for idx in (None, -1, True, 1.2, "1")
        ],
        {"rows": [{"row_idx": 0, "row": None}]},
        *[{"rows": [{"row_idx": 0, "row": {"text": text}}]} for text in (5, [], {})],
        {"rows": [{"row_idx": 0, "row": {"text": "ok", "label": []}}]},
    ],
)
def test_malformed_hf_rows_fail_with_controlled_error(payload):
    with pytest.raises(ExternalServiceError):
        hf(payload).fetch(1)


def test_hf_missing_and_null_text_are_skipped_and_null_label_stays_null():
    source = hf(
        {
            "rows": [
                {"row_idx": 0, "row": {}},
                {"row_idx": 1, "row": {"text": None}},
                {"row_idx": 2, "row": {"text": "valid", "label": None}},
            ]
        }
    )
    dataset = source.fetch(1)
    assert len(dataset.records) == 1 and dataset.records[0].text == "valid"
    assert dataset.records[0].label is None and dataset.filtered_out == {"empty_text": 2}


def test_prefix_warning_survives_public_projection_and_label_report():
    from sentiment_prep.analysis.comprehend_text import TRUNCATION_WARNING
    from sentiment_prep.models import ImpactReport
    from sentiment_prep.presentation import public_report
    from sentiment_prep.report import render_report

    projected = public_report(
        ImpactReport(steps=[], warnings=[TRUNCATION_WARNING, "secret diagnostic"])
    )
    assert projected.warnings == [
        TRUNCATION_WARNING,
        "Some optional analysis results are unavailable.",
    ]
    bundle = DatasetBundle(dataset_id="warning", original=make_dataset(["é" * 3000]))
    saved, _ = label_with_comprehend(bundle, FakeComprehend(), Settings(_env_file=None), 25)
    assert estimate(saved, Settings(_env_file=None), None).prefix_labels == 1
    assert TRUNCATION_WARNING in render_report(saved)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"data": None, "meta": {}},
        {"data": [], "meta": []},
        {"data": [], "meta": {"next_token": 4}},
    ],
)
def test_x_invalid_page_hierarchy_is_controlled(payload):
    with pytest.raises(ExternalServiceError, match="malformed"):
        XSearchSource(guard(), x_client([httpx.Response(200, json=payload)])).fetch(10, query="q")


@pytest.mark.parametrize("mode", ["static", "profile", "default"])
def test_ssm_and_runtime_use_the_same_credential_selection(monkeypatch, mode):
    from sentiment_prep import aws
    from sentiment_prep.api import deps

    calls = []

    def session(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            client=lambda *a, **k: SimpleNamespace(
                get_parameter=lambda **k: {"Parameter": {"Value": "valid-secret"}}
            )
        )

    monkeypatch.setattr(aws.boto3, "Session", session)
    settings = Settings(
        _env_file=None,
        aws_access_key_id="key" if mode == "static" else None,
        aws_secret_access_key="secret" if mode == "static" else None,
        aws_profile="profile" if mode != "default" else None,
        aws_session_token=None,
        api_key=None,
        api_key_ssm_path="/secret",
    )
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    deps.boto_session.cache_clear()
    try:
        deps.boto_session()
        assert settings.resolve_api_key() == "valid-secret"
        assert calls[0] == calls[1]
        assert ("aws_access_key_id" in calls[0]) == (mode == "static")
        assert ("profile_name" in calls[0]) == (mode == "profile")
    finally:
        deps.boto_session.cache_clear()
