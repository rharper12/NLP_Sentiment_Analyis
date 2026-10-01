"""Shared credential selection for runtime AWS clients and SSM configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import SSOTokenLoadError, TokenRetrievalError, UnauthorizedSSOTokenError

if TYPE_CHECKING:
    from sentiment_prep.config import Settings

SSO_LOGIN_HINT = (
    "Your AWS SSO session is likely expired or unavailable. "
    "Run `make aws-login` from the project root, then `make aws-check` before retrying."
)


def is_sso_session_error(error: Exception) -> bool:
    """Recognize SSO failures without exposing provider text or blaming IAM/network errors."""
    return isinstance(error, (SSOTokenLoadError, UnauthorizedSSOTokenError)) or (
        isinstance(error, TokenRetrievalError) and error.kwargs.get("provider") == "sso"
    )


def create_aws_session(settings: Settings) -> Any:
    """Use explicit keys, then the configured profile, then the default credential chain."""
    if settings.has_static_keys:
        return boto3.Session(
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            aws_session_token=settings.aws_session_token,
            region_name=settings.aws_region,
        )
    return (
        boto3.Session(profile_name=settings.aws_profile)
        if settings.aws_profile
        else boto3.Session()
    )
