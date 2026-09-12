import pytest

from sentiment_prep.errors import ValidationError
from sentiment_prep.models import Dataset, Record
from sentiment_prep.preprocessing import DEFAULT_ORDER, STEP_REGISTRY, Pipeline
from sentiment_prep.preprocessing.lemmatize import LemmatizeStep
from sentiment_prep.preprocessing.lowercase import LowercaseStep
from sentiment_prep.preprocessing.missing_data import MissingDataStep
from sentiment_prep.preprocessing.punctuation import clean_text
from sentiment_prep.preprocessing.stopwords import StopwordStep
from sentiment_prep.preprocessing.tokenize import TokenizeStep
from tests.conftest import make_dataset


def rec(text: str) -> Record:
    return Record(id="x", text=text, source_type="csv")


def test_missing_data_drop_and_fill():
    drop = MissingDataStep("drop")
    assert drop.transform(rec("")) is None
    assert drop.transform(rec("   ")) is None
    assert drop.transform(rec("ok")) is not None
    filled = MissingDataStep("fill").transform(rec(""))
    assert filled is not None and filled.text == "[EMPTY]"


def test_lowercase_preserves_tokens_shape():
    out = LowercaseStep().transform(rec("Hello WORLD"))
    assert out is not None and out.text == "hello world" and out.tokens is None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Check https://x.com/a?b=1 now!!!", "Check now"),
        ("@user thanks #happy", "thanks happy"),
        ("don't stop", "don't stop"),
        ("", ""),
        ("こんにちは!", "こんにちは"),
    ],
)
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


def test_tokenize_sets_tokens_and_splits_contractions():
    out = TokenizeStep().transform(rec("I don't like it."))
    assert out is not None
    assert out.tokens is not None and "n't" in out.tokens
    assert out.text == " ".join(out.tokens)


def test_stopwords_keeps_negations_by_default():
    out = StopwordStep().transform(rec("this is not good"))
    assert out is not None and out.tokens == ["not", "good"]
    naive = StopwordStep(keep_negations=False).transform(rec("this is not good"))
    assert naive is not None and naive.tokens == ["good"]


def test_stopwords_all_stopwords_yields_empty():
    out = StopwordStep().transform(rec("the and of"))
    assert out is not None and out.tokens == [] and out.text == ""


def test_lemmatize_collapses_plurals():
    out = LemmatizeStep().transform(rec("cats houses running"))
    assert out is not None and out.tokens[:2] == ["cat", "house"]


def test_step_result_statistics(dataset: Dataset):
    processed, result = MissingDataStep().apply(dataset)
    assert result.records_in == 8 and result.records_out == 6
    assert len(processed.records) == 6
    # input untouched
    assert len(dataset.records) == 8


def test_pipeline_default_order_runs_all_steps(dataset: Dataset):
    processed, results = Pipeline([STEP_REGISTRY[n]() for n in DEFAULT_ORDER]).run(dataset)
    assert [r.step_name for r in results] == DEFAULT_ORDER
    assert all(r.tokens is not None for r in processed.records)
    assert results[-1].vocab_after <= results[0].vocab_before


def test_pipeline_is_fast_enough():
    import time

    big = make_dataset(
        [f"Record number {i} was absolutely wonderful and I loved it!" for i in range(600)]
    )
    started = time.perf_counter()
    Pipeline([STEP_REGISTRY[n]() for n in DEFAULT_ORDER]).run(big)
    assert time.perf_counter() - started < 5


def test_missing_data_fill_value_is_configurable_and_validated():
    """The placeholder becomes a real token, so a blank one must be refused, not silently used."""
    filled = MissingDataStep("fill", fill_value="<<none>>").transform(rec("   "))
    assert filled is not None and filled.text == "<<none>>"
    # Surrounding whitespace is trimmed rather than turned into a token with spaces in it.
    assert MissingDataStep("fill", fill_value="  [NA]  ").transform(rec("")).text == "[NA]"
    for bad in ("", "   ", "x" * 41):
        with pytest.raises(ValidationError):
            MissingDataStep("fill", fill_value=bad)
    # The value is irrelevant when dropping, so it is not validated there.
    assert MissingDataStep("drop", fill_value="").transform(rec("ok")) is not None
