"""Cooperative HTTP work slices, leaving time to persist and release dataset claims.

Socket timeouts bound each provider call; SDK retries are disabled. Before starting another
call, loops reserve sixteen seconds for that call, durable progress, and response cleanup.
This is a resume boundary, not a background thread that keeps billing after HTTP returns.
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass

from botocore.config import Config

from sentiment_prep.errors import AppError

REQUEST_SECONDS = 22.0
CALL_AND_SAVE_SECONDS = 16.0
AWS_CONFIG = Config(connect_timeout=1, read_timeout=3, retries={"total_max_attempts": 1})


class BudgetExhaustedError(AppError):
    """The caller can resume persisted progress in a new HTTP request."""

    status_code = 503


@dataclass(frozen=True)
class RequestBudget:
    """Monotonic deadline inherited by FastAPI's worker threads."""

    deadline: float

    def available(self, seconds: float = CALL_AND_SAVE_SECONDS) -> bool:
        return self.deadline - time.monotonic() > seconds


current_budget: ContextVar[RequestBudget | None] = ContextVar("request_budget", default=None)


def can_start() -> bool:
    """Whether another network unit fits with its persistence margin."""
    budget = current_budget.get()
    return budget is None or budget.available()


def require_budget() -> None:
    """Stop before beginning more network work, never halfway through saving its result."""
    if not can_start():
        raise BudgetExhaustedError("Request slice completed; resume to continue saved work")
