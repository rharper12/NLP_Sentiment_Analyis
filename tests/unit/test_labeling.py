import datetime as dt

import pytest

from sentiment_prep.analysis.comprehend_scorer import billable_units, estimate_cost
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
    price_per_unit_usd=0.0001,
    unit="Unit",
    sku="SENT",
    region="us-east-1",
    fetched_at=dt.datetime.now(dt.UTC),
    status="live",
)


def bundle(texts=None) -> DatasetBundle:
    return DatasetBundle(dataset_id="lab1", original=make_dataset(texts))


def clear_price_cache() -> None:
    from sentiment_prep.history.db import session
    from sentiment_prep.history.models import PriceQuoteRow

    with session() as s:
        s.query(PriceQuoteRow).delete()


def test_billable_units_respects_minimum_and_rounding():
    assert billable_units("short", 100, 3) == 3
    assert billable_units("x" * 301, 100, 3) == 4
    units, cost = estimate_cost(["short", "x" * 301], 0.0001, 100, 3)
    assert (units, cost) == (7, 0.0007)


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
    assert quote and quote.sku == "SENT" and quote.price_per_unit_usd == 0.00012

    live = current_rate(fake, "us-east-1", cache_hours=24)
    assert live and live.status == "live" and fake.calls == 2
    cached = current_rate(fake, "us-east-1", cache_hours=24)
    assert cached and cached.status == "cached" and fake.calls == 2  # served from the database

    stale = current_rate(FakePricing(fail=True), "us-east-1", cache_hours=0)
    assert stale and stale.status == "stale" and stale.price_per_unit_usd == 0.00012

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
