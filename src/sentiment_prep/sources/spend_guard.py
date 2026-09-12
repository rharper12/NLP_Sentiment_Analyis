"""Hard caps on paid API reads.

The X API bills every returned post. A pagination bug that loops forever would run up a bill,
so reads are reserved *before* each request and refused once either cap is reached. The daily
counter lives behind a ``SpendLedger`` protocol; production uses the database-backed ledger in
``history.services`` so a Lambda cold start cannot reset the day's total, and the reservation is
atomic so concurrent invocations cannot both spend the last of the budget.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from sentiment_prep.logging_config import get_logger

logger = get_logger(__name__)


class SpendLedger(Protocol):
    """Persistent daily read counter with an atomic reservation.

    ``reserve`` is the whole point of this protocol: it must increment the day's total and decide
    whether the cap allows it in **one** operation, so two processes cannot both pass a check that
    only one of them should. Implementations that cannot do that are not safe to use with money.
    """

    def get(self, day: str) -> int:
        """Reads already committed for ``day``."""
        ...

    def reserve(self, day: str, reads: int, max_per_day: int) -> bool:
        """Atomically claim ``reads`` against the day's cap.

        Returns:
            True if the reservation fits under ``max_per_day`` and has been recorded, False if it
            does not fit. Never partially reserves.
        """
        ...

    def settle(self, day: str, reserved: int, actual: int, query: str) -> int:
        """Reconcile a reservation once the real count is known.

        ``actual`` is at most ``reserved`` (a page can return fewer posts than requested); the
        difference is released back to the day's budget.

        Returns:
            The day's committed total after settling.
        """
        ...


class InMemoryLedger:
    """Process-local ledger for local development and tests.

    Atomic in the sense that matters here: a single process with the GIL cannot interleave the
    read and the write below. It is *not* shared between processes, so it must not be used where
    more than one worker can bill the same account.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def get(self, day: str) -> int:
        return self._counts.get(day, 0)

    def reserve(self, day: str, reads: int, max_per_day: int) -> bool:
        if self._counts.get(day, 0) + reads > max_per_day:
            return False
        self._counts[day] = self._counts.get(day, 0) + reads
        return True

    def settle(self, day: str, reserved: int, actual: int, query: str) -> int:
        self._counts[day] = max(0, self._counts.get(day, 0) - (reserved - actual))
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
        query: str = "",
    ) -> None:
        self._ledger = ledger
        self._max_per_fetch = max_per_fetch
        self._max_per_day = max_per_day
        self._cost_per_read = Decimal(str(cost_per_read_usd))
        self._query = query
        self._reserved = 0
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
        """Claim budget for a request that may return up to ``reads`` posts.

        The daily claim is made *before* the request goes out and is atomic, so concurrent fetches
        cannot both pass a check only one of them should. Anything not used is released by
        ``record``. The per-fetch cap needs no locking: one guard serves one fetch.

        Raises:
            SpendCapReachedError: if either cap would be exceeded. Nothing is reserved in that case.
        """
        if self.reads_this_fetch + self._reserved + reads > self._max_per_fetch:
            raise SpendCapReachedError(f"per-fetch cap of {self._max_per_fetch} reads reached")
        if not self._ledger.reserve(self.today(), reads, self._max_per_day):
            used_today = self._ledger.get(self.today())
            raise SpendCapReachedError(
                f"daily cap of {self._max_per_day} reads reached ({used_today} used today)"
            )
        self._reserved += reads

    def record(self, reads: int) -> None:
        """Settle the outstanding reservation against the reads that actually happened.

        Args:
            reads: Posts the API returned, never more than the amount reserved.
        """
        reserved, self._reserved = self._reserved, 0
        self.reads_this_fetch += reads
        total_today = self._ledger.settle(self.today(), reserved, reads, self._query)
        logger.info(
            "x_reads_consumed",
            reads=reads,
            released=reserved - reads,
            reads_this_fetch=self.reads_this_fetch,
            reads_today=total_today,
            estimated_cost_usd=float(Decimal(self.reads_this_fetch) * self._cost_per_read),
        )

    def release(self) -> None:
        """Give back an unused reservation, e.g. when a request fails before any read is billed."""
        if self._reserved:
            self.record(0)
