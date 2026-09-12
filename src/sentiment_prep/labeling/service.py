"""Labelling operations on a bundle.

Comprehend labelling runs in slices (``max_records`` per call) so the client can show progress
and cancel, and so a crash loses at most one slice of paid labels. Manual labels always win:
applying one sets ``label_source="manual"`` and keeps ``comprehend_label`` for comparison.
Every function returns a new bundle; nothing here mutates its input. Dollar figures are only
produced when a current rate from the Price List API is supplied; otherwise they are null.
"""

from __future__ import annotations

import random
from collections import Counter
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from sentiment_prep.analysis.comprehend_scorer import ComprehendScorer, cost_for_units, count_units
from sentiment_prep.config import Settings
from sentiment_prep.errors import ValidationError
from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import DatasetBundle, LabelSummary, Record, SentimentLabel
from sentiment_prep.pricing.comprehend_price import PriceQuote, PriceStatus

if TYPE_CHECKING:
    from mypy_boto3_comprehend.client import ComprehendClient

logger = get_logger(__name__)

ReviewMode = Literal["none", "all", "sample"]
SampleUnit = Literal["count", "percent"]


class LabelEstimate(BaseModel):
    """Units Comprehend would bill, and dollars when a current rate is known.

    ``estimated_cost_usd`` and ``cost_per_unit_usd`` are null when the Price List lookup failed
    and nothing is cached; ``price_status`` explains why the UI must not show a figure.
    """

    records_total: int
    records_unlabelled: int
    records_to_send: int
    billable_units: int
    unit_chars: int
    min_units_per_document: int
    estimated_cost_usd: float | None
    cost_per_unit_usd: float | None
    price_status: PriceStatus
    price_fetched_at: str | None
    price_region: str


class LabelProgress(BaseModel):
    """Result of one Comprehend slice. ``cost_usd`` is null when no rate is known."""

    labelled_in_call: int
    labelled_total: int
    remaining: int
    units_billed: int
    cost_usd: float | None
    done: bool


class ManualLabel(BaseModel):
    """One reviewer decision."""

    id: str
    label: SentimentLabel


def needs_comprehend(record: Record) -> bool:
    """Records that have never been sent to Comprehend."""
    return record.comprehend_label is None


def _units(records: list[Record], settings: Settings) -> int:
    return count_units(
        (r.text for r in records), settings.comprehend_unit_chars, settings.comprehend_min_units
    )


def _dollars(units: int, rate: PriceQuote | None) -> float | None:
    """Cost in dollars, or ``None`` when no current rate is known.

    Computed in ``Decimal`` and quantised once; converted to ``float`` only here, at the edge, so
    the value is JSON-friendly without letting float error accumulate across slices.
    """
    return float(cost_for_units(units, rate.price_per_unit)) if rate else None


def estimate(bundle: DatasetBundle, settings: Settings, rate: PriceQuote | None) -> LabelEstimate:
    """Units and (when a rate is known) dollars to label every record Comprehend has not seen."""
    pending = [r for r in bundle.original.records if needs_comprehend(r)]
    units = _units(pending, settings)
    return LabelEstimate(
        records_total=len(bundle.original.records),
        records_unlabelled=sum(1 for r in bundle.original.records if r.label is None),
        records_to_send=len(pending),
        billable_units=units,
        unit_chars=settings.comprehend_unit_chars,
        min_units_per_document=settings.comprehend_min_units,
        estimated_cost_usd=_dollars(units, rate),
        cost_per_unit_usd=float(rate.price_per_unit) if rate else None,
        price_status=rate.status if rate else "unavailable",
        price_fetched_at=rate.fetched_at.isoformat() if rate else None,
        price_region=settings.aws_region,
    )


def label_with_comprehend(
    bundle: DatasetBundle,
    client: ComprehendClient,
    settings: Settings,
    max_records: int,
    rate: PriceQuote | None = None,
) -> tuple[DatasetBundle, LabelProgress]:
    """Send up to ``max_records`` pending records to Comprehend and store the results.

    A record that already has a source or manual label keeps it; Comprehend's answer is recorded
    alongside so the two can be compared. A record with no label adopts Comprehend's.
    """
    pending = [r for r in bundle.original.records if needs_comprehend(r)][:max_records]
    if not pending:
        return bundle, _progress(bundle, 0, 0, _dollars(0, rate))

    results = ComprehendScorer(client).label_texts([r.text for r in pending])
    by_id = dict(zip((r.id for r in pending), results, strict=True))
    updated: list[Record] = []
    for record in bundle.original.records:
        result = by_id.get(record.id)
        if result is None or result.label == "error":
            updated.append(record)
            continue
        changes: dict[str, Any] = {
            "comprehend_label": result.label,
            "comprehend_confidence": result.confidence,
        }
        if record.label is None:
            changes.update(
                label=result.label, label_source="comprehend", label_confidence=result.confidence
            )
        updated.append(record.model_copy(update=changes))

    units = _units(pending, settings)
    cost = _dollars(units, rate)
    new_bundle = _with_records(bundle, updated)
    logger.info("comprehend_labels_applied", records=len(pending), units=units, cost_usd=cost)
    return new_bundle, _progress(new_bundle, len(pending), units, cost)


def apply_manual_labels(bundle: DatasetBundle, items: list[ManualLabel]) -> DatasetBundle:
    """Set reviewer labels. Unknown ids are rejected so a stale UI cannot silently no-op."""
    by_id = {item.id: item.label for item in items}
    unknown = sorted(set(by_id) - {r.id for r in bundle.original.records})
    if unknown:
        raise ValidationError(f"unknown record ids: {unknown[:5]}")
    updated = [
        r.model_copy(
            update={"label": by_id[r.id], "label_source": "manual", "label_confidence": None}
        )
        if r.id in by_id
        else r
        for r in bundle.original.records
    ]
    logger.info("manual_labels_applied", records=len(by_id))
    return _with_records(bundle, updated)


def choose_review(
    bundle: DatasetBundle, mode: ReviewMode, size: int, unit: SampleUnit, seed: int
) -> DatasetBundle:
    """Fix the set of records to review, in a stable order for the given seed.

    ``size`` is a count or a percent of the dataset depending on ``unit``. Sampling is uniform;
    stratifying by Comprehend label would hide the classes Comprehend gets wrong most.
    """
    records = bundle.original.records
    if mode == "none":
        ids: list[str] = []
    elif mode == "all":
        ids = [r.id for r in records]
    else:
        if unit == "percent":
            if not 0 < size <= 100:
                raise ValidationError("percent must be between 1 and 100")
            size = max(1, round(len(records) * size / 100))
        if size < 1:
            raise ValidationError("sample size must be at least 1")
        ids = [r.id for r in random.Random(seed).sample(records, min(size, len(records)))]
    logger.info("review_sample_chosen", mode=mode, size=len(ids))
    return bundle.model_copy(update={"review_ids": ids})


def review_page(bundle: DatasetBundle, offset: int, limit: int) -> tuple[int, list[Record]]:
    """A page of the review set in review order, with the total for the pager."""
    by_id = {r.id: r for r in bundle.original.records}
    ids = bundle.review_ids[offset : offset + limit]
    return len(bundle.review_ids), [by_id[i] for i in ids if i in by_id]


def summary(bundle: DatasetBundle) -> LabelSummary:
    """Counts by source and label; review progress; reviewer/Comprehend agreement."""
    records = bundle.original.records
    review = set(bundle.review_ids)
    both = [r for r in records if r.label_source == "manual" and r.comprehend_label]
    agreed = sum(1 for r in both if r.label == r.comprehend_label)
    return LabelSummary(
        total=len(records),
        labelled=sum(1 for r in records if r.label),
        by_source=dict(Counter(r.label_source for r in records if r.label_source)),
        by_label=dict(Counter(r.label for r in records if r.label)),
        review_sample_size=len(review),
        reviewed=sum(1 for r in records if r.id in review and r.label_source == "manual"),
        manual_vs_comprehend_agreement=round(agreed / len(both), 4) if both else None,
        disagreements=len(both) - agreed,
    )


def _progress(bundle: DatasetBundle, in_call: int, units: int, cost: float | None) -> LabelProgress:
    remaining = sum(1 for r in bundle.original.records if needs_comprehend(r))
    return LabelProgress(
        labelled_in_call=in_call,
        labelled_total=len(bundle.original.records) - remaining,
        remaining=remaining,
        units_billed=units,
        cost_usd=cost,
        done=remaining == 0,
    )


def _with_records(bundle: DatasetBundle, records: list[Record]) -> DatasetBundle:
    return bundle.model_copy(
        update={"original": bundle.original.model_copy(update={"records": records})}
    )
