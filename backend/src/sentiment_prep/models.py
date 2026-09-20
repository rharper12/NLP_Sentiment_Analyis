"""Pydantic models shared across every layer.

These are the contracts between sources, preprocessing, labelling, analysis and the API.
Changing a field here is an architectural decision, not a local edit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sentiment_prep.errors import ValidationError

SourceType = Literal["x", "huggingface", "csv"]
CheckpointStage = Literal["collected", "processed", "labelled"]
LabelSource = Literal["source", "comprehend", "manual"]
SentimentLabel = Literal["positive", "negative", "neutral", "mixed"]
SENTIMENT_LABELS: tuple[str, ...] = ("positive", "negative", "neutral", "mixed")


class Record(BaseModel):
    """One text item.

    ``label`` is the label to train on; ``label_source`` says where it came from so Task 2 can
    train on Comprehend labels and evaluate on manually reviewed ones. ``comprehend_label`` is
    kept separately so a manual override never erases what Comprehend said. ``tokens`` is
    populated once a tokenising step has run.
    """

    id: str
    text: str
    label: str | None = None
    label_source: LabelSource | None = None
    label_confidence: float | None = None
    comprehend_label: str | None = None
    comprehend_confidence: float | None = None
    source_type: SourceType
    created_at: datetime | None = None
    tokens: list[str] | None = None

    def words(self) -> list[str]:
        """Return tokens if present, otherwise a whitespace split. Used for statistics."""
        return self.tokens if self.tokens is not None else self.text.split()


class Dataset(BaseModel):
    """A fetched collection of records plus enough provenance for the write-up."""

    model_config = ConfigDict(revalidate_instances="always")

    records: list[Record]
    source_type: SourceType
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    query: str | None = None
    # Search window actually used, so a write-up can state the range rather than guess it.
    window_start: datetime | None = None
    window_end: datetime | None = None
    truncated_reason: str | None = None
    # Why posts the source returned were not kept, e.g. {"no_content_after_cleaning": 34}.
    # Recorded at collection so the write-up can state the denominator honestly: these rows
    # never reach the pipeline, so its per-step counts do not account for them.
    filtered_out: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        """Reject ambiguous identities before records enter joins, review or persistence."""
        seen: dict[str, int] = {}
        for row, record in enumerate(self.records, start=1):
            if record.id in seen:
                raise ValidationError(
                    f"duplicate record id {record.id!r} at record {row}; "
                    f"first used at record {seen[record.id]}"
                )
            seen[record.id] = row
        return self

    def __len__(self) -> int:
        return len(self.records)


class StepMetrics(BaseModel):
    """What one preprocessing step did to the dataset."""

    step_name: str
    records_in: int
    records_out: int
    vocab_before: int
    vocab_after: int
    avg_tokens_before: float
    avg_tokens_after: float
    sample_diffs: list[tuple[str, str]] = Field(default_factory=list, max_length=5)


class StepResult(StepMetrics):
    """Internal step metrics including operator timing."""

    duration_ms: float


class SentimentComparison(BaseModel):
    """Baseline scorer output before vs after preprocessing."""

    agreement: float | None = None
    comparable_records: int = 0
    shared_records: int = 0
    distribution_before: dict[str, int]
    distribution_after: dict[str, int]


class ReportContent(BaseModel):
    """Everything the UI and the written report need about a preprocessing run.

    Optional fields are ``None`` when the corresponding AWS service was disabled or failed;
    the ``warnings`` list says which and why.
    """

    sentiment: SentimentComparison | None = None
    embedding_drift: float | None = None
    explanation: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ImpactReport(ReportContent):
    """Stored report including operator step timings."""

    steps: list[StepResult]


class LabelSummary(BaseModel):
    """Label coverage, provenance, review progress and reviewer/Comprehend agreement."""

    total: int
    labelled: int
    by_source: dict[str, int]
    by_label: dict[str, int]
    review_sample_size: int
    reviewed: int
    manual_vs_comprehend_agreement: float | None
    disagreements: int
    manually_reviewed: int = 0
    machine_scored: int = 0
    comparable_records: int = 0
    agreements: int = 0
    warnings: list[str] = Field(default_factory=list)


class LabelFailure(BaseModel):
    """A persisted document failure; raw service messages stay in operator logs."""

    record_id: str
    code: Literal["document_rejected", "temporarily_unavailable", "missing_result"]
    retryable: bool
    attempts: int = Field(ge=1, le=3)


class CachedSentiment(BaseModel):
    """Persisted analysis result and finite attempts, keyed by submitted text hash."""

    label: str
    confidence: float | None = None
    error_code: Literal["document_rejected", "temporarily_unavailable", "missing_result"] | None = (
        None
    )
    retryable: bool = False
    attempts: int = 1


class AnalysisProgress(BaseModel):
    """Resume optional analysis without repeating committed provider results."""

    signature: str = ""
    completed: list[str] = Field(default_factory=list)
    sentiment: dict[str, CachedSentiment] = Field(default_factory=dict)
    vectors: dict[str, list[float]] = Field(default_factory=dict)
    attempts: dict[str, int] = Field(default_factory=dict)
    partial: bool = False


class CollectionProgress(BaseModel):
    """Stable X request/cursor. Provider-response-before-commit crashes remain ambiguous."""

    request: dict[str, str | int | None]
    next_token: str | None = None
    reads: int = 0
    # Missing historical accounting is unknown, never silently reported as zero.
    billed_reads: int | None = None
    committed_cost_usd: Decimal | None = None
    complete: bool = False
    stop_reason: str | None = None
    retry_at: float = 0


class CheckpointState(BaseModel):
    """Content fingerprint of the stored snapshot, with explicit replacement/freshness state."""

    revision: str | None = None
    status: Literal["current", "stale", "failed"] = "stale"


class DatasetBundle(BaseModel):
    """The unit of storage: original data, the latest processed run, labels and checkpoints.

    ``review_ids`` is the subset chosen for manual review, in review order. ``checkpoints``
    maps each stage (``collected``, ``processed``, ``labelled``) to the URI of its CSV snapshot.
    """

    dataset_id: str
    original: Dataset
    processed: Dataset | None = None
    applied_steps: list[str] = Field(default_factory=list)
    report: ImpactReport | None = None
    review_ids: list[str] = Field(default_factory=list)
    label_failures: dict[str, LabelFailure] = Field(default_factory=dict)
    checkpoints: dict[CheckpointStage, str] = Field(default_factory=dict)
    checkpoint_status: dict[str, CheckpointState] = Field(default_factory=dict)
    collection: CollectionProgress | None = None
    analysis: AnalysisProgress = Field(default_factory=AnalysisProgress)
