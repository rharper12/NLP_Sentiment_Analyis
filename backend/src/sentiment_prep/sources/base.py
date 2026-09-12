"""The interface every data source implements."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from sentiment_prep.models import Dataset


class DataSource(Protocol):
    """Fetch up to ``limit`` records.

    Implementations return whatever they managed to collect and set
    ``Dataset.truncated_reason`` when they stopped early, rather than raising. Callers
    decide whether a short dataset is acceptable. ``should_stop`` is polled between pages so a
    cancelled request stops spending money at the next page boundary.
    """

    name: str

    def fetch(
        self,
        limit: int,
        query: str | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> Dataset: ...
