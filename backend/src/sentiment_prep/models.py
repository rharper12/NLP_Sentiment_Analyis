"""Pydantic models shared across every layer.

These are the contracts between sources, preprocessing, labelling, analysis and the API.
Changing a field here is an architectural decision, not a local edit.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sentiment_prep.errors import ValidationError
from sentiment_prep.filenames import FileStem

SourceType = Literal["x", "huggingface", "csv"]
CheckpointStage = Literal["collected", "processed", "labelled"]
LabelSource = Literal["source", "comprehend", "manual"]
SentimentLabel = Literal["positive", "negative", "neutral", "mixed"]
SENTIMENT_LABELS: tuple[str, ...] = ("positive", "negative", "neutral", "mixed")
EligibilityDecision = Literal["include", "exclude", "pending"]
EligibilityReason = Literal[
    "news_or_article",
    "giveaway_or_promotion",
    "technical_developer_content",
    "duplicate_or_repeated_template",
    "off_topic",
    "insufficient_context",
    "not_english",
]


def calendar_bounds(start: date, end: date, timezone: str) -> tuple[datetime, datetime]:
    """Resolve inclusive calendar dates to UTC without assuming fixed-length DST days."""
    if start > end:
        raise ValueError("The start date must come before the final included date")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Use a valid IANA timezone, such as America/Chicago") from exc
    try:
        return (
            datetime.combine(start, time.min, zone).astimezone(UTC),
            datetime.combine(end + timedelta(days=1), time.min, zone).astimezone(UTC),
        )
    except OverflowError as exc:
        raise ValueError("Calendar dates exceed the supported timestamp range") from exc


class ConsumerPolicy(BaseModel):
    """Immutable selection context; changing it requires a new collection."""

    # Missing versions belong to the original product-specific policy; never migrate on read.
    version: Literal["consumer-reactions-v1", "consumer-reactions-v2"] = "consumer-reactions-v1"
    start_date: date
    end_date: date
    timezone: str = "America/Chicago"
    per_author_limit: int = Field(default=2, ge=1, le=100)
    reviewed_target: int = Field(default=500, ge=1, le=5000)
    duplicate_threshold: float = Field(default=0.9, gt=0, le=1)
    selection_rule: Literal["daily-quotas-recency;author-earliest-id"] = (
        "daily-quotas-recency;author-earliest-id"
    )

    @model_validator(mode="after")
    def valid_calendar(self) -> Self:
        """Reject invalid zones and oversized/reversed windows before any provider work."""
        self.bounds()
        if not 0 <= (self.end_date - self.start_date).days < 31:
            raise ValueError("Select an ordered range of at most 31 calendar days")
        return self

    def bounds(self) -> tuple[datetime, datetime]:
        """Convert local midnights separately so DST days can be 23 or 25 hours."""
        return calendar_bounds(self.start_date, self.end_date, self.timezone)


class ScreeningSuggestion(BaseModel):
    """Explainable machine suggestion, never evidence of completed human review."""

    decision: EligibilityDecision
    reason: EligibilityReason | None = None
    evidence: list[str] = Field(default_factory=list)
    duplicate_of: str | None = None
    policy_version: str = "consumer-reactions-v1"


class EligibilityReview(BaseModel):
    """Recoverable operator decision and its explanation; no identity inference."""

    decision: EligibilityDecision
    reason: EligibilityReason | None = None
    note: str = ""
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PostReference(BaseModel):
    """Relationships supplied by X, without fetching related posts."""

    id: str = Field(strict=True)
    type: Literal["replied_to", "quoted", "retweeted"]


class PostUrl(BaseModel):
    """Only URL evidence needed for review; linked pages are never fetched."""

    url: str
    expanded_url: str | None = None
    display_url: str | None = None


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
    author_id: str | None = Field(default=None, strict=True)
    lang: str | None = None
    conversation_id: str | None = Field(default=None, strict=True)
    in_reply_to_user_id: str | None = Field(default=None, strict=True)
    references: list[PostReference] = Field(default_factory=list)
    urls: list[PostUrl] = Field(default_factory=list)
    screening: ScreeningSuggestion | None = None
    eligibility: EligibilityDecision = Field(default_factory=lambda: "pending")
    eligibility_reviewed: bool = Field(default_factory=bool)
    eligibility_reason: EligibilityReason | None = None
    eligibility_note: str = Field(default_factory=str)
    eligibility_history: list[EligibilityReview] = Field(default_factory=list)
    sentiment_reviewed: bool = Field(default_factory=bool)
    sentiment_reviewed_at: datetime | None = None

    def with_manual_label(self, label: SentimentLabel) -> Record:
        """Confirm a human label while retaining the original automated suggestion."""
        return self.model_copy(
            update={
                "label": label,
                "label_source": "manual",
                "label_confidence": None,
                "sentiment_reviewed": True,
                "sentiment_reviewed_at": datetime.now(UTC),
            }
        )

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
    consumer_policy: ConsumerPolicy | None = None

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
    attempts: dict[str, int] = Field(default_factory=dict)
    partial: bool = False


class CollectionSlice(BaseModel):
    """One local calendar day's independent provider cursor and candidate quota."""

    day: date
    start: datetime
    end: datetime
    next_token: str | None = None
    exhausted: bool = False


class CollectionProgress(BaseModel):
    """Stable X request/cursor. Provider-response-before-commit crashes remain ambiguous."""

    request: dict[str, str | int | float | bool | None]
    next_token: str | None = None
    # Bind pagination tokens to their endpoint across request slices.
    search_mode: Literal["recent", "all"] | None = None
    reads: int = 0
    # Missing historical accounting is unknown, never silently reported as zero.
    billed_reads: int | None = None
    committed_cost_usd: Decimal | None = None
    # Saved rows added per user request; old collections have no recoverable batch history.
    first_batch_saved: int | None = None
    last_batch_saved: int | None = None
    complete: bool = False
    stop_reason: str | None = None
    retry_at: float = 0
    slices: list[CollectionSlice] = Field(default_factory=list)
    candidate_target: int | None = None
    seen_ids: list[str] = Field(default_factory=list)
    retrieved: int = 0
    terminal_error: bool = False

    def needs_more_records(self, retained: int) -> bool:
        """Include old jobs completed before final duplicate removal reduced their count."""
        target = self.request.get("limit")
        return not self.complete or (
            self.next_token is not None
            and self.stop_reason is None
            and isinstance(target, int)
            and retained < target
        )


class CheckpointState(BaseModel):
    """Content fingerprint of the stored snapshot, with explicit replacement/freshness state."""

    revision: str | None = None
    status: Literal["current", "stale", "failed"] = "stale"
    failure_code: Literal["sso_session_unavailable"] | None = None


class DatasetBundle(BaseModel):
    """The unit of storage: original data, the latest processed run, labels and checkpoints.

    ``review_ids`` is the subset chosen for manual review, in review order. ``checkpoints``
    maps each stage (``collected``, ``processed``, ``labelled``) to the URI of its CSV snapshot.
    """

    dataset_id: str
    file_stem: FileStem | None = None
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
