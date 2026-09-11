"""What we keep about each dataset, pipeline run and paid API call.

Metadata only: record text stays in the working repository (S3/in-memory), never in the database.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base shared by the three tables."""


class DatasetRun(Base):
    """One dataset load or upload."""

    __tablename__ = "dataset_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(16))
    query: Mapped[str] = mapped_column(Text, default="")
    record_count: Mapped[int] = mapped_column(Integer)
    truncated_reason: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    runs: Mapped[list[PipelineRun]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )


class PipelineRun(Base):
    """One execution of the preprocessing pipeline against a dataset."""

    __tablename__ = "pipeline_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_pk: Mapped[int] = mapped_column(
        ForeignKey("dataset_run.id", ondelete="CASCADE"), index=True
    )
    applied_steps: Mapped[list[str]] = mapped_column(JSON, default=list)
    records_out: Mapped[int] = mapped_column(Integer)
    vocab_before: Mapped[int] = mapped_column(Integer)
    vocab_after: Mapped[int] = mapped_column(Integer)
    sentiment_agreement: Mapped[float | None] = mapped_column(Float, nullable=True)
    embedding_drift: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    dataset: Mapped[DatasetRun] = relationship(back_populates="runs")


class SpendEntry(Base):
    """Billable X reads, appended as they happen. Aggregated for caps and the spend panel."""

    __tablename__ = "spend_entry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[dt.date] = mapped_column(Date, index=True)
    reads: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    query: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PriceQuoteRow(Base):
    """Last successful Price List lookup per service and region."""

    __tablename__ = "price_quote"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(64), index=True)
    region: Mapped[str] = mapped_column(String(32), index=True)
    # Numeric, not Float: this is money and it round-trips to the API as a Decimal.
    price_per_unit_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    unit: Mapped[str] = mapped_column(String(64), default="")
    sku: Mapped[str] = mapped_column(String(64), default="")
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
