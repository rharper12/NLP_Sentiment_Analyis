"""Hard caps on paid API reads.

The X API bills every returned post. A pagination bug that loops forever would run up a bill,
so reads are reserved *before* each request and refused once either cap is reached. The daily
counter lives behind a ``SpendLedger`` protocol; production uses the database-backed ledger in
``history.services`` so a Lambda cold start cannot reset the day's total.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sentiment_prep.logging_config import get_logger

logger = get_logger(__name__)


class SpendLedger(Protocol):
    """Persistent daily read counter."""

    def get(self, day: str) -> int: ...

    def add(self, day: str, reads: int) -> int: ...


class InMemoryLedger:
    """Process-local ledger for local development and tests."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def get(self, day: str) -> int:
        return self._counts.get(day, 0)

    def add(self, day: str, reads: int) -> int:
        self._counts[day] = self.get(day) + reads
        return self._counts[day]


class SpendCapReachedError(Exception):
    """Raised by ``reserve`` when a request would exceed a cap. Carries the reason for the UI."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SpendGuard:
    """Tracks reads for one fetch and enforces per-fetch and per-day caps."""

    def __init__(
        self,
        ledger: SpendLedger,
        max_per_fetch: int,
        max_per_day: int,
        cost_per_read_usd: float,
    ) -> None:
        self._ledger = ledger
        self._max_per_fetch = max_per_fetch
        self._max_per_day = max_per_day
        self._cost_per_read = cost_per_read_usd
        self.reads_this_fetch = 0

    @staticmethod
    def today() -> str:
        """UTC date key used for the daily ledger."""
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def remaining(self) -> int:
        """How many more reads this fetch may make before a cap is reached."""
        fetch_left = self._max_per_fetch - self.reads_this_fetch
        day_left = self._max_per_day - self._ledger.get(self.today())
        return max(0, min(fetch_left, day_left))

    def reserve(self, reads: int) -> None:
        """Check a request of ``reads`` results fits under both caps, else raise."""
        if self.reads_this_fetch + reads > self._max_per_fetch:
            raise SpendCapReachedError(f"per-fetch cap of {self._max_per_fetch} reads reached")
        used_today = self._ledger.get(self.today())
        if used_today + reads > self._max_per_day:
            raise SpendCapReachedError(
                f"daily cap of {self._max_per_day} reads reached ({used_today} used today)"
            )

    def record(self, reads: int) -> None:
        """Account for reads that actually happened."""
        self.reads_this_fetch += reads
        total_today = self._ledger.add(self.today(), reads)
        logger.info(
            "x_reads_consumed",
            reads=reads,
            reads_this_fetch=self.reads_this_fetch,
            reads_today=total_today,
            estimated_cost_usd=round(self.reads_this_fetch * self._cost_per_read, 4),
        )
