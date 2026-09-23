"""Operator authentication and short-lived, in-memory browser sessions."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Annotated

from fastapi import Depends, Header
from pydantic import BaseModel, Field, SecretStr

from sentiment_prep.config import Settings, get_settings
from sentiment_prep.errors import AppError, NotFoundError
from sentiment_prep.logging_config import get_logger

logger = get_logger(__name__)
HEADER = "X-API-Key"
SESSION_SECONDS = 3600


class UnauthorizedError(AppError):
    """Missing, invalid or expired authorization."""

    status_code = 401


class SessionRequest(BaseModel):
    """Operator credential, submitted once over HTTPS and never stored by the browser."""

    key: SecretStr = Field(min_length=1, max_length=4096)


class SessionResponse(BaseModel):
    """Short-lived bearer credential. Kept only in browser memory."""

    token: str
    expires_in: int = SESSION_SECONDS


def _signature(payload: str, key: str) -> str:
    return hmac.new(key.encode(), f"browser-session:{payload}".encode(), hashlib.sha256).hexdigest()


def create_session(key: str, settings: Settings) -> SessionResponse:
    """Exchange a valid operator key for an expiring session."""
    expected = settings.resolve_api_key()
    if not expected or not secrets.compare_digest(key.encode(), expected.encode()):
        raise UnauthorizedError("Invalid operator credential")
    payload = f"{int(time.time()) + SESSION_SECONDS}.{secrets.token_hex(24)}"
    return SessionResponse(token=f"{payload}.{_signature(payload, expected)}")


def _valid_session(token: str, key: str) -> bool:
    parts = token.split(".")
    if len(parts) != 3:
        return False
    expiry, nonce, signature = parts
    if not expiry.isascii() or not expiry.isdecimal() or len(expiry) > 12:
        return False
    payload = f"{expiry}.{nonce}"
    return int(expiry) > time.time() and secrets.compare_digest(
        signature.encode(), _signature(payload, key).encode()
    )


def require_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias=HEADER)] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Accept operator scripts or an expiring browser session; Lambda always fails closed."""
    expected = settings.resolve_api_key()
    if not expected and settings.runtime == "local":
        return
    if expected:
        if x_api_key and secrets.compare_digest(x_api_key.encode(), expected.encode()):
            return
        if (
            authorization
            and authorization.startswith("Bearer ")
            and _valid_session(authorization.removeprefix("Bearer "), expected)
        ):
            return
    logger.warning("request_unauthorized")
    raise UnauthorizedError("Authorization required or session expired")


def require_diagnostics(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    """Operator-only routes are unavailable when diagnostics are disabled."""
    if not settings.diagnostics_enabled:
        raise NotFoundError("Not found")


def require_local_datasets(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    """Prevent the deployed runtime from browsing local dataset files."""
    if not settings.local_datasets_available:
        raise NotFoundError("Not found")
