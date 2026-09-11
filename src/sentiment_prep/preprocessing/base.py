"""Base class for preprocessing steps.

A step transforms one ``Record`` at a time and returns ``None`` to drop it. The base class
handles iteration, timing and the before/after statistics so subclasses stay tiny.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import ClassVar

from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import Dataset, Record, StepResult

logger = get_logger(__name__)

SAMPLE_DIFFS = 5


class PreprocessStep(ABC):
    """Pure transformation over a dataset: input is never mutated."""

    name: ClassVar[str]

    @abstractmethod
    def transform(self, record: Record) -> Record | None:
        """Return a new record, or ``None`` to drop this one."""

    def apply(self, dataset: Dataset) -> tuple[Dataset, StepResult]:
        """Run ``transform`` over every record and describe what changed."""
        started = time.perf_counter()
        vocab_before: set[str] = set()
        vocab_after: set[str] = set()
        tokens_before = 0
        tokens_after = 0
        kept: list[Record] = []
        diffs: list[tuple[str, str]] = []

        for record in dataset.records:
            before_words = record.words()
            vocab_before.update(before_words)
            tokens_before += len(before_words)

            result = self.transform(record)
            if result is None:
                continue
            after_words = result.words()
            vocab_after.update(after_words)
            tokens_after += len(after_words)
            kept.append(result)
            if len(diffs) < SAMPLE_DIFFS and result.text != record.text:
                diffs.append((record.text, result.text))

        count_in = len(dataset.records)
        count_out = len(kept)
        step_result = StepResult(
            step_name=self.name,
            records_in=count_in,
            records_out=count_out,
            vocab_before=len(vocab_before),
            vocab_after=len(vocab_after),
            avg_tokens_before=round(tokens_before / count_in, 2) if count_in else 0.0,
            avg_tokens_after=round(tokens_after / count_out, 2) if count_out else 0.0,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            sample_diffs=diffs,
        )
        logger.info("step_applied", **step_result.model_dump(exclude={"sample_diffs"}))
        return dataset.model_copy(update={"records": kept}), step_result
