import datetime as dt
from decimal import Decimal

import pytest

from sentiment_prep.analysis.comprehend_scorer import billable_units, cost_for_units, count_units
from sentiment_prep.config import Settings
from sentiment_prep.errors import ValidationError
from sentiment_prep.export.parquet_export import csv_to_parquet
from sentiment_prep.labeling import service as labeling
from sentiment_prep.labeling.service import ManualLabel
from sentiment_prep.models import DatasetBundle
from sentiment_prep.pricing.comprehend_price import PriceQuote, current_rate, fetch_rate
from sentiment_prep.storage.checkpoints import LocalCheckpointStore, checkpoint_bundle
from tests.conftest import FakeComprehend, FakePricing, make_dataset

SETTINGS = Settings(comprehend_unit_chars=100, comprehend_min_units=3)
RATE = PriceQuote(
    price_per_unit=Decimal("0.0001"),
    unit="Unit",
    sku="SENT",
    region="us-east-1",
    fetched_at=dt.datetime.now(dt.UTC),
    status="live",
)


def bundle(texts=None, dataset_id="lab1") -> DatasetBundle:
    return DatasetBundle(dataset_id=dataset_id, original=make_dataset(texts))


def clear_price_cache() -> None:
    from sentiment_prep.history.db import session
    from sentiment_prep.history.models import PriceQuoteRow

    with session() as s:
        s.query(PriceQuoteRow).delete()


def test_billable_units_respects_minimum_and_rounding():
    assert billable_units("short", 100, 3) == 3
    assert billable_units("x" * 301, 100, 3) == 4
    assert count_units(["short", "x" * 301], 100, 3) == 7
    assert cost_for_units(7, Decimal("0.0001")) == Decimal("0.0007")


def test_money_does_not_accumulate_float_error():
    """Three slices at the same price must sum to the exact published total."""
    slices = [cost_for_units(9, Decimal("0.0001")) for _ in range(3)]
    assert sum(slices) == Decimal("0.0027")


def test_estimate_counts_only_records_comprehend_has_not_seen():
    b = bundle(["a loved b", "c terrible d"])
    est = labeling.estimate(b, SETTINGS, RATE)
    assert est.records_to_send == 2 and est.billable_units == 6 and est.estimated_cost_usd == 0.0006
    assert est.price_status == "live"
    b, _ = labeling.label_with_comprehend(b, FakeComprehend(), SETTINGS, max_records=1)
    assert labeling.estimate(b, SETTINGS, RATE).records_to_send == 1


def test_estimate_without_rate_reports_unavailable_not_a_guess():
    est = labeling.estimate(bundle(["a loved b"]), SETTINGS, None)
    assert est.billable_units == 3
    assert est.estimated_cost_usd is None and est.cost_per_unit_usd is None
    assert est.price_status == "unavailable"


def test_price_lookup_picks_standard_sentiment_sku_and_caches():
    clear_price_cache()
    fake = FakePricing(price=0.00012)
    quote = fetch_rate(fake, "us-east-1")
    assert quote and quote.sku == "SENT" and quote.price_per_unit == Decimal("0.00012")

    live = current_rate(fake, "us-east-1", cache_hours=24)
    assert live and live.status == "live" and fake.calls == 2
    cached = current_rate(fake, "us-east-1", cache_hours=24)
    assert cached and cached.status == "cached" and fake.calls == 2  # served from the database

    stale = current_rate(FakePricing(fail=True), "us-east-1", cache_hours=0)
    assert stale and stale.status == "stale" and stale.price_per_unit == Decimal("0.00012")

    clear_price_cache()
    assert current_rate(FakePricing(fail=True), "us-east-1", cache_hours=24) is None
    assert current_rate(None, "us-east-1", cache_hours=24) is None


def test_comprehend_slices_are_resumable_and_keep_source_labels():
    b = bundle(["I loved it", "terrible stuff", "plain text here"])
    b.original.records[2] = b.original.records[2].model_copy(
        update={"label": "neutral", "label_source": "source"}
    )
    b, p1 = labeling.label_with_comprehend(b, FakeComprehend(), SETTINGS, max_records=2, rate=RATE)
    assert p1.labelled_in_call == 2 and not p1.done and p1.remaining == 1 and p1.cost_usd == 0.0006
    b, p2 = labeling.label_with_comprehend(b, FakeComprehend(), SETTINGS, max_records=10)
    assert p2.done and p2.labelled_in_call == 1 and p2.cost_usd is None
    r0, r2 = b.original.records[0], b.original.records[2]
    assert (
        r0.label == "positive" and r0.label_source == "comprehend" and r0.label_confidence == 0.85
    )
    assert (
        r2.label == "neutral" and r2.label_source == "source" and r2.comprehend_label == "neutral"
    )
    _, p3 = labeling.label_with_comprehend(b, FakeComprehend(), SETTINGS, max_records=10)
    assert p3.labelled_in_call == 0 and p3.units_billed == 0


def test_manual_labels_override_and_summary_measures_agreement():
    b = bundle(["I loved it", "terrible stuff", "plain text here"])
    b, _ = labeling.label_with_comprehend(b, FakeComprehend(), SETTINGS, max_records=10)
    b = labeling.choose_review(b, "sample", 2, "count", seed=1)
    ids = b.review_ids
    b = labeling.apply_manual_labels(b, [ManualLabel(id=ids[0], label="mixed")])
    s = labeling.summary(b)
    assert s.review_sample_size == 2 and s.reviewed == 1
    assert s.manual_vs_comprehend_agreement == 0.0 and s.disagreements == 1
    assert s.by_source == {"comprehend": 2, "manual": 1}
    with pytest.raises(ValidationError):
        labeling.apply_manual_labels(b, [ManualLabel(id="nope", label="positive")])


def test_review_modes():
    b = bundle([f"text number {i}" for i in range(20)])
    assert len(labeling.choose_review(b, "all", 1, "count", 1).review_ids) == 20
    assert len(labeling.choose_review(b, "none", 1, "count", 1).review_ids) == 0
    assert len(labeling.choose_review(b, "sample", 25, "percent", 1).review_ids) == 5
    assert (
        labeling.choose_review(b, "sample", 5, "count", 9).review_ids
        == labeling.choose_review(b, "sample", 5, "count", 9).review_ids
    )
    with pytest.raises(ValidationError):
        labeling.choose_review(b, "sample", 150, "percent", 1)
    total, page = labeling.review_page(labeling.choose_review(b, "all", 1, "count", 1), 5, 3)
    assert total == 20 and [r.id for r in page] == ["r5", "r6", "r7"]


def test_local_checkpoints_and_parquet_conversion(tmp_path):
    store = LocalCheckpointStore(tmp_path)
    b = checkpoint_bundle(store, bundle(["one two three"]), "collected")
    assert b.checkpoints["collected"].endswith("collected.csv")
    csv_bytes = store.read("lab1", "collected", "csv")
    assert csv_bytes and csv_bytes.startswith("\ufeff".encode())
    info = store.save("lab1", "collected", "parquet", csv_to_parquet(csv_bytes))
    assert info.format == "parquet" and info.bytes > 0
    assert {(c.stage, c.format) for c in store.list("lab1")} == {
        ("collected", "csv"),
        ("collected", "parquet"),
    }
    assert store.read("lab1", "labelled", "csv") is None


def test_s3_checkpoints_use_multipart_capable_upload(s3_bucket):
    from sentiment_prep.storage.checkpoints import S3CheckpointStore

    client, bucket = s3_bucket
    store = S3CheckpointStore(bucket, "checkpoints", client)
    store.save("lab1", "labelled", "csv", b"\xef\xbb\xbfid,text\n1,hi\n")
    assert store.read("lab1", "labelled", "csv") is not None
    listed = store.list("lab1")
    assert listed and listed[0].uri == f"s3://{bucket}/checkpoints/lab1/labelled.csv"


def test_in_memory_repository_evicts_oldest_bundle():
    """Bounded so a long-lived local process cannot hold every dataset ever loaded."""
    from sentiment_prep.errors import NotFoundError
    from sentiment_prep.storage.repository import InMemoryRepository

    repo = InMemoryRepository(max_datasets=2)
    for name in ("a", "b", "c"):
        repo.save(bundle(["one two three"], dataset_id=name))
    assert repo.get("c") and repo.get("b")
    with pytest.raises(NotFoundError):
        repo.get("a")


class _FakeBotoError(Exception):
    """Stand-in for a botocore error, which carries its code in a response dict."""

    def __init__(self, code: str = "") -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


def test_expired_sso_session_is_reported_as_something_the_operator_can_fix():
    """An expired SSO token is the likeliest AWS failure; a bare 500 would hide the remedy."""
    from sentiment_prep.api.aws_errors import translated
    from sentiment_prep.errors import CredentialsError

    expired = type("UnauthorizedSSOTokenError", (Exception,), {})
    with pytest.raises(CredentialsError) as caught, translated("Amazon Comprehend"):
        raise expired()
    assert "aws sso login" in str(caught.value)


def test_permission_and_throttling_failures_name_the_cause():
    from sentiment_prep.api.aws_errors import translated
    from sentiment_prep.errors import ExternalServiceError

    with pytest.raises(ExternalServiceError, match="not allowed"), translated("Amazon Comprehend"):
        raise _FakeBotoError("AccessDeniedException")
    with pytest.raises(ExternalServiceError, match="throttling"), translated("Amazon Comprehend"):
        raise _FakeBotoError("ThrottlingException")


def test_unrecognised_failures_are_left_alone():
    """Only failures with a known remedy are rewritten; anything else keeps its own traceback."""
    from sentiment_prep.api.aws_errors import translated

    with pytest.raises(_FakeBotoError), translated("Amazon Comprehend"):
        raise _FakeBotoError("SomethingNewAndUnmapped")


def test_agreement_uses_all_unique_manual_machine_pairs_across_review_selections():
    from sentiment_prep.labeling.service import (
        ManualLabel,
        apply_manual_labels,
        choose_review,
        summary,
    )
    from sentiment_prep.models import DatasetBundle
    from sentiment_prep.report import render_report
    from tests.conftest import make_dataset

    bundle = DatasetBundle(dataset_id="cohort", original=make_dataset(["a", "b", "c", "d"]))
    for record in bundle.original.records[:3]:
        record.comprehend_label = "positive"
    bundle = apply_manual_labels(
        bundle,
        [
            ManualLabel(id="r0", label="positive"),
            ManualLabel(id="r1", label="negative"),
            ManualLabel(id="r3", label="neutral"),
        ],
    )
    bundle = choose_review(bundle, "none", 1, "count", 7)
    counts = summary(bundle)
    assert counts.reviewed == 0
    assert (
        counts.manually_reviewed,
        counts.machine_scored,
        counts.comparable_records,
        counts.agreements,
    ) == (3, 3, 2, 1)
    assert counts.manual_vs_comprehend_agreement == 0.5
    assert "over 2 comparable records" in render_report(bundle)
    corrected = apply_manual_labels(bundle, [ManualLabel(id="r1", label="positive")])
    counts = summary(choose_review(corrected, "all", 1, "count", 7))
    assert counts.reviewed == 3 and counts.comparable_records == 2
    assert counts.agreements == 2 and counts.manual_vs_comprehend_agreement == 1
    only_manual = apply_manual_labels(
        DatasetBundle(dataset_id="manual", original=make_dataset(["a"])),
        [ManualLabel(id="r0", label="neutral")],
    )
    assert summary(only_manual).comparable_records == 0
    assert summary(only_manual).manual_vs_comprehend_agreement is None
    assert "agreement:" not in render_report(only_manual)
