"""Run an ordered list of steps and collect their results."""

from __future__ import annotations

import time
from collections.abc import Sequence

from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import Dataset, StepResult
from sentiment_prep.preprocessing.base import PreprocessStep

logger = get_logger(__name__)


class Pipeline:
    """Steps are applied in the order given; the caller controls that order."""

    def __init__(self, steps: Sequence[PreprocessStep]) -> None:
        self._steps = list(steps)

    def run(self, dataset: Dataset) -> tuple[Dataset, list[StepResult]]:
        """Return the transformed dataset and one ``StepResult`` per step."""
        started = time.perf_counter()
        results: list[StepResult] = []
        current = dataset
        for step in self._steps:
            current, result = step.apply(current)
            results.append(result)
        logger.info(
            "pipeline_complete",
            steps=[s.name for s in self._steps],
            records_in=len(dataset.records),
            records_out=len(current.records),
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return current, results
