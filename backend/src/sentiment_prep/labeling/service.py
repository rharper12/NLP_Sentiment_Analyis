"""Labelling operations on a bundle.

Comprehend labelling runs in slices (``max_records`` per call) so the client can show progress
and cancel. Each batch is persisted before the next. A response lost before commit may need
to be re-billed on recovery. Manual labels always win:
applying one sets ``label_source="manual"`` and keeps ``comprehend_label`` for comparison.
Every function returns a new bundle; nothing here mutates its input. Dollar figures are only
produced when a current rate from the Price List API is supplied; otherwise they are null.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from sentiment_prep.analysis.comprehend_scorer import ComprehendScorer, cost_for_units, count_units
from sentiment_prep.analysis.comprehend_text import prepare_text
from sentiment_prep.budget import can_start
from sentiment_prep.config import Settings
from sentiment_prep.errors import ValidationError
from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import DatasetBundle, LabelFailure, LabelSummary, Record, SentimentLabel
from sentiment_prep.pricing.comprehend_price import PriceQuote, PriceStatus

if TYPE_CHECKING:
    from mypy_boto3_comprehend.client import ComprehendClient

logger = get_logger(__name__)

ReviewMode = Literal["none", "all", "sample"]
SampleUnit = Literal["count", "percent"]
MAX_LABEL_ATTEMPTS = 3


class LabelEstimate(BaseModel):
    """Units Comprehend would bill, and dollars when a current rate is known.

    ``estimated_cost_usd`` and ``cost_per_unit_usd`` are null when the Price List lookup failed
    and nothing is cached; ``price_status`` explains why the UI must not show a figure.
    """

    records_total: int
    records_unlabelled: int
    truncated_records: int = 0
    prefix_labels: int = 0
    records_to_send: int
    failed_total: int = 0
    billable_units: int
    unit_chars: int
    min_units_per_document: int
    estimated_cost_usd: float | None
    cost_per_unit_usd: float | None
    price_status: PriceStatus
    price_fetched_at: str | None
    price_region: str | None = None


class LabelProgress(BaseModel):
    """Result of one Comprehend slice. ``cost_usd`` is null when no rate is known."""

    truncated_records: int = 0
    labelled_in_call: int
    attempted_in_call: int = 0
    failed_in_call: int = 0
    failed_total: int = 0
    failures: list[LabelFailure] = Field(default_factory=list)
    labelled_total: int
    remaining: int
    units_billed: int
    cost_usd: float | None
    done: bool
    partial: bool = False
    stop_reason: str | None = None


class ManualLabel(BaseModel):
    """One reviewer decision."""

    id: str
    label: SentimentLabel


def needs_comprehend(record: Record) -> bool:
    """Records without a successful Comprehend label."""
    return record.comprehend_label is None


def _pending(bundle: DatasetBundle) -> list[Record]:
    def eligible(record: Record) -> bool:
        failure = bundle.label_failures.get(record.id)
        return needs_comprehend(record) and (
            failure is None or (failure.retryable and failure.attempts < MAX_LABEL_ATTEMPTS)
        )

    return [r for r in bundle.original.records if eligible(r)]


def _units(records: list[Record], settings: Settings) -> int:
    return count_units(
        (r.text for r in records), settings.comprehend_unit_chars, settings.comprehend_min_units
    )


def _dollars(units: int, rate: PriceQuote | None) -> float | None:
    """Cost in dollars, or ``None`` when no current rate is known.

    Computed in ``Decimal`` and quantised once; converted to ``float`` only here, at the edge, so
    the value is JSON-friendly without letting float error accumulate across slices.
    """
    if rate is None:
        return None
    try:
        cost = float(cost_for_units(units, rate.price_per_unit))
    except ArithmeticError:
        logger.warning("comprehend_price_arithmetic_unavailable")
        return None
    return cost if math.isfinite(cost) else None


def estimate(bundle: DatasetBundle, settings: Settings, rate: PriceQuote | None) -> LabelEstimate:
    """Units and (when a rate is known) dollars to label every record Comprehend has not seen."""
    pending = _pending(bundle)
    units = _units(pending, settings)
    cost = _dollars(units, rate)
    if cost is None:
        rate = None
    return LabelEstimate(
        records_total=len(bundle.original.records),
        records_unlabelled=sum(1 for r in bundle.original.records if r.label is None),
        records_to_send=len(pending),
        truncated_records=sum(prepare_text(r.text).truncated for r in pending),
        prefix_labels=sum(
            prepare_text(r.text).truncated
            for r in bundle.original.records
            if r.comprehend_label is not None
        ),
        failed_total=len(bundle.label_failures),
        billable_units=units,
        unit_chars=settings.comprehend_unit_chars,
        min_units_per_document=settings.comprehend_min_units,
        estimated_cost_usd=cost,
        cost_per_unit_usd=float(rate.price_per_unit) if rate else None,
        price_status=rate.status if rate else "unavailable",
        price_fetched_at=rate.fetched_at.isoformat() if rate else None,
        price_region=settings.aws_region if settings.diagnostics_enabled else None,
    )


def label_with_comprehend(
    bundle: DatasetBundle,
    client: ComprehendClient,
    settings: Settings,
    max_records: int,
    rate: PriceQuote | None = None,
    *,
    persist: Callable[[DatasetBundle], None] | None = None,
) -> tuple[DatasetBundle, LabelProgress]:
    """Commit each paid batch before starting another; resume skips committed successes.

    Provider success followed by process termination before commit is inherently ambiguous.
    There is no exactly-once billing guarantee; the repository's abandoned claim requires
    operator inspection rather than automatically repeating uncertain work.
    """
    attempted = succeeded = units = 0
    reason = None
    # Snapshot eligible IDs once: retries never loop within the same request.
    ids = [r.id for r in _pending(bundle)[:max_records]]
    for offset in range(0, len(ids), 25):
        if not can_start():
            reason = "request_budget"
            break
        batch_ids = set(ids[offset : offset + 25])
        bundle, progress = _label_batch(bundle, client, settings, batch_ids, rate)
        if persist is not None:
            persist(bundle)
        attempted += progress.attempted_in_call
        succeeded += progress.labelled_in_call
        units += progress.units_billed
        if progress.failed_in_call:
            reason = "upstream_failure"
            break
    result = _progress(bundle, succeeded, units, _dollars(units, rate), attempted=attempted)
    result.partial = bool(result.remaining or result.failed_total)
    result.stop_reason = reason or ("more_records" if result.remaining else None)
    return bundle, result


def _label_batch(
    bundle: DatasetBundle,
    client: ComprehendClient,
    settings: Settings,
    batch_ids: set[str],
    rate: PriceQuote | None = None,
) -> tuple[DatasetBundle, LabelProgress]:
    """Send up to ``max_records`` pending records to Comprehend and store the results.

    A record that already has a source or manual label keeps it; Comprehend's answer is recorded
    alongside so the two can be compared. A record with no label adopts Comprehend's.
    """
    pending = [r for r in _pending(bundle) if r.id in batch_ids]
    if not pending:
        return bundle, _progress(bundle, 0, 0, _dollars(0, rate))

    results = ComprehendScorer(client).label_texts([r.text for r in pending])
    by_id = dict(zip((r.id for r in pending), results, strict=True))
    updated: list[Record] = []
    failures = dict(bundle.label_failures)
    succeeded = 0
    for record in bundle.original.records:
        result = by_id.get(record.id)
        if result is None:
            updated.append(record)
            continue
        if result.label == "error":
            previous = failures.get(record.id)
            attempts = previous.attempts + 1 if previous else 1
            failures[record.id] = LabelFailure(
                record_id=record.id,
                code=result.error_code or "missing_result",
                retryable=result.retryable,
                attempts=attempts,
            )
            updated.append(record)
            continue
        succeeded += 1
        failures.pop(record.id, None)
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
    new_bundle = _with_records(bundle, updated).model_copy(update={"label_failures": failures})
    logger.info(
        "comprehend_labels_applied",
        records=succeeded,
        attempted=len(pending),
        units=units,
        cost_usd=cost,
    )
    return new_bundle, _progress(new_bundle, succeeded, units, cost, attempted=len(pending))


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


def _progress(
    bundle: DatasetBundle, in_call: int, units: int, cost: float | None, *, attempted: int = 0
) -> LabelProgress:
    remaining = len(_pending(bundle))
    return LabelProgress(
        truncated_records=sum(
            prepare_text(r.text).truncated
            for r in bundle.original.records
            if r.comprehend_label is not None
        ),
        labelled_in_call=in_call,
        labelled_total=sum(1 for r in bundle.original.records if not needs_comprehend(r)),
        attempted_in_call=attempted,
        failed_in_call=attempted - in_call,
        failed_total=len(bundle.label_failures),
        failures=list(bundle.label_failures.values()),
        remaining=remaining,
        units_billed=units,
        cost_usd=cost,
        done=remaining == 0,
    )


def _with_records(bundle: DatasetBundle, records: list[Record]) -> DatasetBundle:
    return bundle.model_copy(
        update={"original": bundle.original.model_copy(update={"records": records})}
    )
