"""Translate botocore failures into messages that name the fix.

A Comprehend call fails for a small number of reasons in practice, and they need different
actions: an SSO session that has lapsed needs ``aws sso login``, a missing model grant needs a
console change, a throttle needs patience. Left unhandled, all of them surface as HTTP 500
"internal error", which tells an operator nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from botocore.exceptions import BotoCoreError

from sentiment_prep.errors import ConfigurationError, CredentialsError, ExternalServiceError
from sentiment_prep.logging_config import get_logger

logger = get_logger(__name__)

# botocore raises these by name rather than by a shared base class, so matching on the class name
# avoids importing half of botocore just to catch them.
_CREDENTIAL_ERRORS = frozenset(
    {
        "NoCredentialsError",
        "PartialCredentialsError",
        "CredentialRetrievalError",
        "SSOTokenLoadError",
        "UnauthorizedSSOTokenError",
        "TokenRetrievalError",
        "ProfileNotFound",
        "ExpiredToken",
        "ExpiredTokenException",
        "InvalidClientTokenId",
        "UnrecognizedClientException",
    }
)


@contextmanager
def translated(service: str) -> Iterator[None]:
    """Re-raise AWS failures from ``service`` as errors with an actionable message.

    Args:
        service: Human-readable service name, used in the message shown to the operator.

    Raises:
        CredentialsError: credentials are absent, expired, or the profile does not exist.
        ExternalServiceError: the service rejected or failed the request for any other reason.
    """
    try:
        yield
    except Exception as error:
        name = type(error).__name__
        code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
        if name in _CREDENTIAL_ERRORS or code in _CREDENTIAL_ERRORS:
            logger.error("aws_credentials_unusable", service=service, error=name, exc_info=True)
            raise CredentialsError(
                f"AWS credentials for {service} are missing or expired. For an SSO profile run "
                "`aws sso login --profile <name>`; check AWS_PROFILE and AWS_REGION otherwise."
            ) from error
        if code in ("AccessDeniedException", "AccessDenied", "UnauthorizedOperation"):
            logger.error("aws_access_denied", service=service, code=code, exc_info=True)
            raise ExternalServiceError(
                f"This AWS identity is not allowed to call {service} ({code})."
            ) from error
        if code in ("ThrottlingException", "TooManyRequestsException"):
            raise ExternalServiceError(
                f"{service} is throttling this account. Try again shortly."
            ) from error
        if isinstance(error, BotoCoreError):
            raise ConfigurationError(
                f"Cannot initialize {service}. Check AWS_PROFILE, AWS_REGION and credentials."
            ) from None
        raise
