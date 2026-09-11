"""Pydantic models shared across every layer.

These are the contracts between sources, preprocessing, labelling, analysis and the API.
Changing a field here is an architectural decision, not a local edit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceType = Literal["x", "huggingface", "csv"]
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

    records: list[Record]
    source_type: SourceType
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    query: str | None = None
    truncated_reason: str | None = None

    def __len__(self) -> int:
        return len(self.records)


class StepResult(BaseModel):
    """What one preprocessing step did to the dataset."""

    step_name: str
    records_in: int
    records_out: int
    vocab_before: int
    vocab_after: int
    avg_tokens_before: float
    avg_tokens_after: float
    duration_ms: float
    sample_diffs: list[tuple[str, str]] = Field(default_factory=list, max_length=5)


class SentimentComparison(BaseModel):
    """Baseline scorer output before vs after preprocessing."""

    agreement: float
    distribution_before: dict[str, int]
    distribution_after: dict[str, int]


class ImpactReport(BaseModel):
    """Everything the UI and the written report need about a preprocessing run.

    Optional fields are ``None`` when the corresponding AWS service was disabled or failed;
    the ``warnings`` list says which and why.
    """

    steps: list[StepResult]
    sentiment: SentimentComparison | None = None
    embedding_drift: float | None = None
    explanation: str | None = None
    warnings: list[str] = Field(default_factory=list)


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
    checkpoints: dict[str, str] = Field(default_factory=dict)
