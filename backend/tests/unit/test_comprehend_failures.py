"""Document failures must neither invent progress nor cause unbounded paid retries."""

import pytest
from botocore.exceptions import ClientError

from sentiment_prep.analysis.comprehend_scorer import ComprehendScorer
from sentiment_prep.config import Settings
from sentiment_prep.labeling.service import estimate, label_with_comprehend
from sentiment_prep.models import DatasetBundle
from tests.conftest import make_dataset


class FailingComprehend:
    def __init__(self, mode="permanent"):
        self.mode = mode
        self.calls = 0
        self.submitted = []

    def batch_detect_sentiment(self, TextList, LanguageCode):
        self.calls += 1
        self.submitted.extend(TextList)
        if self.mode == "transport" or (self.mode == "later_batch" and self.calls > 1):
            raise ClientError({"Error": {"Code": "ThrottlingException"}}, "BatchDetectSentiment")
        results, errors = [], []
        for i, text in enumerate(TextList):
            if text.startswith("good") or (self.mode == "recover" and self.calls > 1):
                results.append(
                    {
                        "Index": i,
                        "Sentiment": "POSITIVE",
                        "SentimentScore": {
                            "Positive": 0.9,
                            "Negative": 0.05,
                            "Neutral": 0.03,
                            "Mixed": 0.02,
                        },
                    }
                )
            elif self.mode != "missing":
                errors.append(
                    {
                        "Index": i,
                        "ErrorCode": "TEXT_SIZE_LIMIT_EXCEEDED"
                        if self.mode == "permanent"
                        else "INTERNAL_SERVER_ERROR",
                    }
                )
        return {"ResultList": results, "ErrorList": errors}


def make_bundle(texts):
    return DatasetBundle(dataset_id="failures", original=make_dataset(texts))


@pytest.mark.parametrize(
    "mode,requests,retryable",
    [
        ("permanent", 1, False),
        ("transient", 3, True),
        ("missing", 3, True),
        ("transport", 3, True),
    ],
)
def test_repeated_failed_calls_are_bounded(mode, requests, retryable):
    fake = FailingComprehend(mode)
    bundle = make_bundle(["bad document one", "bad document two"])
    for _ in range(10):
        bundle, progress = label_with_comprehend(bundle, fake, Settings(), 25)
        # Round-trip persistence: a new request cannot reset failure budgets.
        bundle = DatasetBundle.model_validate_json(bundle.model_dump_json())
        assert progress.labelled_in_call == progress.labelled_total == 0
        assert all(r.label is None for r in bundle.original.records)
        assert progress.failed_total == 2
    assert fake.calls == requests
    assert progress.done and progress.remaining == 0
    assert progress.attempted_in_call == 0
    assert all(
        f.retryable is retryable and f.attempts == requests for f in bundle.label_failures.values()
    )
    assert estimate(bundle, Settings(), None).records_to_send == 0


def test_mixed_results_count_and_keep_only_successes():
    fake = FailingComprehend()
    bundle, progress = label_with_comprehend(
        make_bundle(["good result", "bad document"]), fake, Settings(), 25
    )
    assert progress.labelled_in_call == progress.labelled_total == 1
    assert progress.attempted_in_call == 2 and progress.failed_in_call == 1
    assert progress.done and progress.failed_total == 1
    assert bundle.original.records[0].label == "positive"
    assert bundle.original.records[1].label is None
    for _ in range(5):
        bundle, progress = label_with_comprehend(bundle, fake, Settings(), 25)
    assert fake.calls == 1
    assert progress.labelled_in_call == 0 and progress.labelled_total == 1


def test_transient_result_recovers_without_resubmitting_success():
    fake = FailingComprehend("recover")
    bundle, first = label_with_comprehend(
        make_bundle(["good result", "bad temporary"]), fake, Settings(), 25
    )
    assert first.labelled_in_call == 1 and first.remaining == 1
    bundle, second = label_with_comprehend(bundle, fake, Settings(), 25)
    assert second.labelled_in_call == 1 and second.labelled_total == 2 and second.done
    assert second.failed_total == 0 and not bundle.label_failures
    assert fake.submitted.count("good result") == 1 and fake.calls == 2


def test_failure_in_later_batch_keeps_first_batch():
    fake = FailingComprehend("later_batch")
    bundle, progress = label_with_comprehend(
        make_bundle([f"good {i}" for i in range(26)]), fake, Settings(), 100
    )
    assert progress.labelled_in_call == 25 and progress.failed_in_call == 1
    assert sum(r.label is not None for r in bundle.original.records) == 25
    assert fake.calls == 2


def test_zero_successful_comparisons_are_unavailable():
    fake = FailingComprehend()
    dataset = make_dataset(["bad document"])
    comparison = ComprehendScorer(fake).compare(dataset, dataset)
    assert comparison.agreement is None and comparison.comparable_records == 0
    assert comparison.shared_records == 1
    assert comparison.distribution_before == comparison.distribution_after == {}
    assert fake.calls == 1


def test_partial_comparisons_exclude_failure_on_either_side():
    fake = FailingComprehend()
    before = make_dataset(["good unchanged", "bad unchanged", "good before"])
    after = make_dataset(["good unchanged", "bad unchanged", "bad after"])
    comparison = ComprehendScorer(fake).compare(before, after)
    assert comparison.agreement == 1.0 and comparison.comparable_records == 1
    assert comparison.shared_records == 3
    assert comparison.distribution_before == {"positive": 2}
    assert comparison.distribution_after == {"positive": 1}
    assert fake.calls == 2
