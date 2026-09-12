"""Optional shared-secret authentication.

The API spends money: an X search bills the operator's credits and Comprehend labelling bills
their AWS account. A deployment reachable from the internet must therefore require a key. It is
optional rather than mandatory so local development stays frictionless, but the health endpoint
reports whether it is on, and ``deployment.md`` treats leaving it off in Lambda as a mistake.

The comparison is constant-time: a naive ``==`` leaks key length and prefix through timing.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header

from sentiment_prep.config import Settings, get_settings
from sentiment_prep.errors import AppError
from sentiment_prep.logging_config import get_logger

logger = get_logger(__name__)

HEADER = "X-API-Key"


class UnauthorizedError(AppError):
    """The request carried no key, or the wrong one."""

    status_code = 401


def require_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias=HEADER)] = None,
) -> None:
    """Reject the request unless it carries the configured key.

    A no-op when ``API_KEY`` is unset, which is the local default.

    Raises:
        UnauthorizedError: if a key is configured and the header is missing or wrong.
    """
    expected = settings.resolve_api_key()
    if not expected:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        logger.warning("request_unauthorized", has_header=bool(x_api_key))
        raise UnauthorizedError(f"missing or invalid {HEADER} header")
