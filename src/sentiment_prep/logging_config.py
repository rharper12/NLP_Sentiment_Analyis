"""Structured logging with structlog: JSON in Lambda, coloured key=value locally.

structlog plays the role Pino plays in Node: every event is a dict, context is *bound* once
(request id, dataset id) and appears on every subsequent line automatically, and the renderer
is swapped by environment rather than by rewriting call sites. Stdlib loggers from third-party
libraries are routed through the same processors so CloudWatch sees one consistent shape.

Usage::

    from sentiment_prep.logging_config import get_logger
    log = get_logger(__name__)
    log.info("x_fetch_complete", requested=600, returned=512, duration_ms=1834.2)

Event names are snake_case identifiers, not sentences, so they can be filtered in CloudWatch
Logs Insights (``filter event = "x_fetch_complete"``).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, get_contextvars
from structlog.typing import EventDict, WrappedLogger

# Third-party loggers that emit request bodies or per-byte chatter at DEBUG.
_NOISY_LOGGERS = ("botocore", "boto3", "urllib3", "httpx", "httpcore", "sqlalchemy.engine")


def _add_event_key(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Rename structlog's ``event`` to ``message`` so CloudWatch's default view is readable."""
    if "event" in event_dict:
        event_dict["message"] = event_dict.pop("event")
    return event_dict


def _ensure_request_id(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    event_dict.setdefault("request_id", "-")
    return event_dict


def configure_logging(level: str = "INFO", json_output: bool | None = None) -> None:
    """Install processors on both structlog and the stdlib root logger.

    Args:
        level: Root log level name.
        json_output: Force JSON (Lambda/CloudWatch) or console rendering. Defaults to JSON when
            stdout is not a TTY, which is what Lambda and CI look like.
    """
    if json_output is None:
        json_output = not sys.stdout.isatty()

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _ensure_request_id,
    ]
    renderer: Any
    if json_output:
        shared.append(structlog.processors.format_exc_info)
        shared.append(_add_event_key)
        renderer = structlog.processors.JSONRenderer(sort_keys=False, default=str)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    # Lambda's runtime pre-installs a handler that would double-print; uvicorn does the same.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Module logger. Bound context (see ``bind_context``) is merged in automatically."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]


def bind_context(**values: Any) -> None:
    """Attach key/values to every log line for the rest of the current request/task."""
    bind_contextvars(**values)


def clear_context() -> None:
    """Drop all bound context. Called at the start of each request."""
    clear_contextvars()


def current_context() -> Mapping[str, Any]:
    """Read the bound context, e.g. to echo ``request_id`` in a response header."""
    return get_contextvars()


def get_request_id() -> str:
    """The bound request id, or ``-`` outside a request."""
    return str(current_context().get("request_id", "-"))
