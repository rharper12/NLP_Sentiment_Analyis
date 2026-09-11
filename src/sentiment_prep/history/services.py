"""Write and read history. The only module outside ``history`` that touches these tables."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from sentiment_prep.history.db import session
from sentiment_prep.history.models import DatasetRun, PipelineRun, PriceQuoteRow, SpendEntry
from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import DatasetBundle

if TYPE_CHECKING:
    from sentiment_prep.pricing.comprehend_price import PriceQuote

SERVICE_CODE_COMPREHEND = "AmazonComprehend"

logger = get_logger(__name__)


def record_dataset(bundle: DatasetBundle) -> None:
    """Persist metadata for a newly loaded dataset (idempotent on dataset_id)."""
    with session() as s:
        row = s.scalar(select(DatasetRun).where(DatasetRun.dataset_id == bundle.dataset_id))
        if row is None:
            row = DatasetRun(
                dataset_id=bundle.dataset_id,
                source_type=bundle.original.source_type,
                record_count=0,
            )
            s.add(row)
        row.source_type = bundle.original.source_type
        row.query = bundle.original.query or ""
        row.record_count = len(bundle.original.records)
        row.truncated_reason = bundle.original.truncated_reason or ""
    logger.debug("history_dataset_recorded", dataset_id=bundle.dataset_id)


def record_run(bundle: DatasetBundle) -> None:
    """Persist a pipeline run summary; the full report stays with the bundle."""
    if bundle.report is None or bundle.processed is None:
        return
    steps = bundle.report.steps
    with session() as s:
        dataset = s.scalar(select(DatasetRun).where(DatasetRun.dataset_id == bundle.dataset_id))
        if dataset is None:
            dataset = DatasetRun(
                dataset_id=bundle.dataset_id,
                source_type=bundle.original.source_type,
                record_count=len(bundle.original.records),
            )
            s.add(dataset)
            s.flush()
        s.add(
            PipelineRun(
                dataset_pk=dataset.id,
                applied_steps=list(bundle.applied_steps),
                records_out=len(bundle.processed.records),
                vocab_before=steps[0].vocab_before if steps else 0,
                vocab_after=steps[-1].vocab_after if steps else 0,
                sentiment_agreement=bundle.report.sentiment.agreement
                if bundle.report.sentiment
                else None,
                embedding_drift=bundle.report.embedding_drift,
                duration_ms=sum(x.duration_ms for x in steps),
                warnings=list(bundle.report.warnings),
            )
        )
    logger.debug("history_run_recorded", dataset_id=bundle.dataset_id, steps=bundle.applied_steps)


def recent_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Latest pipeline runs joined with their dataset, newest first."""
    with session() as s:
        rows = s.execute(
            select(PipelineRun, DatasetRun)
            .join(DatasetRun, PipelineRun.dataset_pk == DatasetRun.id)
            .order_by(PipelineRun.created_at.desc(), PipelineRun.id.desc())
            .limit(limit)
        ).all()
        return [
            {
                "dataset_id": d.dataset_id,
                "source_type": d.source_type,
                "query": d.query,
                "record_count": d.record_count,
                "applied_steps": r.applied_steps,
                "records_out": r.records_out,
                "vocab_before": r.vocab_before,
                "vocab_after": r.vocab_after,
                "sentiment_agreement": r.sentiment_agreement,
                "embedding_drift": r.embedding_drift,
                "duration_ms": r.duration_ms,
                "created_at": r.created_at.isoformat(),
            }
            for r, d in rows
        ]


def spend_totals(today: dt.date) -> dict[str, Any]:
    """Reads and cost for today and the current calendar month."""
    month_start = today.replace(day=1)
    with session() as s:
        day = s.execute(
            select(
                func.coalesce(func.sum(SpendEntry.reads), 0),
                func.coalesce(func.sum(SpendEntry.cost_usd), 0),
            ).where(SpendEntry.day == today)
        ).one()
        month = s.execute(
            select(
                func.coalesce(func.sum(SpendEntry.reads), 0),
                func.coalesce(func.sum(SpendEntry.cost_usd), 0),
            ).where(SpendEntry.day >= month_start)
        ).one()
    return {
        "today_reads": int(day[0]),
        "today_cost_usd": float(day[1]),
        "month_reads": int(month[0]),
        "month_cost_usd": float(month[1]),
    }


def get_price_quote(service: str, region: str) -> PriceQuote | None:
    """Last cached rate for a service/region, or ``None``."""
    from sentiment_prep.pricing.comprehend_price import PriceQuote

    with session() as s:
        row = s.scalar(
            select(PriceQuoteRow).where(
                PriceQuoteRow.service == service, PriceQuoteRow.region == region
            )
        )
        if row is None:
            return None
        fetched = row.fetched_at
        if fetched.tzinfo is None:  # SQLite drops tzinfo; values are always stored as UTC
            fetched = fetched.replace(tzinfo=dt.UTC)
        return PriceQuote(
            price_per_unit=row.price_per_unit_usd,
            unit=row.unit,
            sku=row.sku,
            region=row.region,
            fetched_at=fetched,
            status="cached",
        )


def put_price_quote(quote: PriceQuote) -> None:
    """Upsert the cached rate."""
    with session() as s:
        row = s.scalar(
            select(PriceQuoteRow).where(
                PriceQuoteRow.service == SERVICE_CODE_COMPREHEND,
                PriceQuoteRow.region == quote.region,
            )
        )
        if row is None:
            row = PriceQuoteRow(service=SERVICE_CODE_COMPREHEND, region=quote.region)
            s.add(row)
        row.price_per_unit_usd = quote.price_per_unit
        row.unit = quote.unit
        row.sku = quote.sku
        row.fetched_at = quote.fetched_at
    logger.debug("price_quote_cached", region=quote.region, price=str(quote.price_per_unit))


class DbLedger:
    """``SpendLedger`` backed by ``spend_entry`` rows.

    Append-only, so concurrent Lambda invocations cannot overwrite each other's counts the way a
    single JSON object in S3 could.
    """

    def __init__(self, cost_per_read_usd: float, query: str = "") -> None:
        self._cost = Decimal(str(cost_per_read_usd))
        self._query = query

    def get(self, day: str) -> int:
        with session() as s:
            total = s.scalar(
                select(func.coalesce(func.sum(SpendEntry.reads), 0)).where(
                    SpendEntry.day == dt.date.fromisoformat(day)
                )
            )
        return int(total or 0)

    def add(self, day: str, reads: int) -> int:
        with session() as s:
            s.add(
                SpendEntry(
                    day=dt.date.fromisoformat(day),
                    reads=reads,
                    cost_usd=self._cost * reads,
                    query=self._query[:500],
                )
            )
        return self.get(day)
