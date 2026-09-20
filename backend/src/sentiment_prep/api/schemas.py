"""Request and response bodies. Kept separate from domain models so the API can evolve."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from sentiment_prep.analysis.metrics import DatasetMetrics
from sentiment_prep.labeling.service import ReviewMode, SampleUnit
from sentiment_prep.models import Record, SentimentLabel, SourceType
from sentiment_prep.preprocessing.missing_data import DEFAULT_FILL_VALUE, MAX_FILL_VALUE_CHARS
from sentiment_prep.presentation import PublicImpactReport
from sentiment_prep.storage.checkpoints import CheckpointInfo


class LoadRequest(BaseModel):
    """Fetch a dataset from a remote source."""

    request_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{8,64}$")
    source: Literal["x", "huggingface"]
    limit: int = Field(
        default=600, ge=1, le=5000, description="Records to fetch. Task 1 needs 500+."
    )
    query: str | None = Field(
        default=None, description="Required for X. Operators like lang:en allowed."
    )
    start_time: datetime | None = Field(
        default=None,
        description="Oldest post to return (X only). Clamped to seven days ago, the limit of "
        "recent search.",
    )
    end_time: datetime | None = Field(
        default=None, description="Newest post to return (X only). Clamped to a few seconds ago."
    )


class DatasetSummary(BaseModel):
    """Enough about a dataset for the UI header and the loader result."""

    dataset_id: str
    source_type: SourceType
    query: str | None
    window_start: datetime | None = None
    window_end: datetime | None = None
    record_count: int
    labelled_count: int
    filtered_out: dict[str, int] = Field(
        default_factory=dict,
        description="Posts the source returned but that were not kept, by reason.",
    )
    truncated_reason: str | None
    estimated_cost_usd: float | None = None
    partial: bool = False
    resume_request_id: str | None = None
    retry_at: float | None = None
    warnings: list[str] = Field(default_factory=list)
    preview: list[Record]


class StepOptions(BaseModel):
    """Per-step knobs the UI exposes. Unset means the step default."""

    missing_data_strategy: Literal["drop", "fill"] = "drop"
    missing_data_fill_value: str = Field(
        default=DEFAULT_FILL_VALUE,
        max_length=MAX_FILL_VALUE_CHARS,
        description=(
            "Placeholder written into empty records when the strategy is 'fill'. It becomes a "
            "token in the vocabulary, so pick something the corpus cannot contain."
        ),
    )
    keep_negations: bool = True


class PreprocessRequest(BaseModel):
    """Run steps in the given order and measure impact."""

    steps: list[str] = Field(min_length=1, description="Step names in execution order.")
    options: StepOptions = Field(default_factory=StepOptions)
    explain: bool = Field(default=True, description="Ask Bedrock for a prose explanation.")


class PreprocessResponse(BaseModel):
    """Processed preview plus the full impact report."""

    dataset_id: str
    applied_steps: list[str]
    record_count: int
    partial: bool = False
    metrics_before: DatasetMetrics
    metrics_after: DatasetMetrics
    report: PublicImpactReport
    preview: list[Record]


class RecordPair(BaseModel):
    """Original next to processed. ``processed`` is null when the record was dropped."""

    original: Record
    processed: Record | None


class RecordPage(BaseModel):
    """A page of original and processed records, joined by id."""

    total: int
    offset: int
    items: list[RecordPair]


class StepInfo(BaseModel):
    """Step catalogue entry for the UI toggle list."""

    name: str
    title: str
    summary: str
    strengths: str
    limitations: str


class ComprehendLabelRequest(BaseModel):
    """One slice of Comprehend labelling. The client loops until ``done``."""

    max_records: int = Field(default=250, ge=1, le=2000)
    confirm_cost: bool = Field(
        default=False, description="Must be true; the UI sets it after the confirmation dialog."
    )


class ReviewRequest(BaseModel):
    """Which records a person will review by hand."""

    mode: ReviewMode
    size: int = Field(default=150, ge=1)
    unit: SampleUnit = "count"
    seed: int = 7


class ReviewPage(BaseModel):
    """A page of records chosen for manual review, in review order."""

    total: int
    offset: int
    items: list[Record]


class ManualLabelItem(BaseModel):
    """One record's reviewed label."""

    id: str
    label: SentimentLabel


class ManualLabelRequest(BaseModel):
    """Reviewer decisions, applied atomically."""

    items: list[ManualLabelItem] = Field(min_length=1, max_length=5000)


class CheckpointList(BaseModel):
    """Stage snapshots stored for a dataset, and where."""

    location: Literal["local", "s3"]
    items: list[CheckpointInfo]


class SaveResponse(BaseModel):
    """Where the saved folder landed."""

    uri: str | None = None


class ErrorResponse(BaseModel):
    """Uniform error body. ``request_id`` lets a user quote the exact log lines."""

    error: str
    request_id: str


class SpendSummary(BaseModel):
    """Local ledger totals plus caps. ``x_usage`` is X's own figure when the tier exposes it."""

    today_reads: int
    today_cost_usd: float
    month_reads: int
    month_cost_usd: float
    cap_per_fetch: int
    cap_per_day: int
    remaining_today: int
    cost_per_read_usd: float
    x_usage: dict[str, object] | None = None
    x_configured: bool


class HistoryRun(BaseModel):
    """One row in the history panel."""

    dataset_id: str
    source_type: str
    query: str
    record_count: int
    applied_steps: list[str]
    records_out: int
    vocab_before: int
    vocab_after: int
    sentiment_agreement: float | None
    embedding_drift: float | None
    duration_ms: float | None = None
    created_at: str


class HealthResponse(BaseModel):
    """Liveness plus, when ``diagnostics`` is true, the facts an operator checks first.

    Operator fields are omitted unless diagnostics are enabled (local runtime by default)
    and the caller is authorized. Public liveness reveals no storage or enabled-service details.
    """

    status: str
    version: str
    diagnostics: bool
    x_configured: bool
    auth_required: bool
    runtime: str | None = None
    database: str | None = None
    database_ephemeral: bool | None = None
    checkpoints: Literal["local", "s3"] | None = None
    comprehend_enabled: bool | None = None
    bedrock_enabled: bool | None = None
