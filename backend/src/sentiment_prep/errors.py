"""Application error types mapped to HTTP responses by the API layer."""


class AppError(Exception):
    """Base class. ``status_code`` decides the HTTP status; ``message`` is safe to show users."""

    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationError(AppError):
    """The request was well-formed but semantically wrong (bad CSV, unknown step)."""

    status_code = 400


class NotFoundError(AppError):
    """A dataset id that does not exist."""

    status_code = 404


class ExternalServiceError(AppError):
    """An upstream API refused or failed.

    Carries a message safe to show a user: which service, what it said, and what to check. Never
    the credentials themselves.
    """

    status_code = 502


class CredentialsError(AppError):
    """AWS credentials are missing, expired, or lack permission.

    Separated from ``ConfigurationError`` because the fix is different: not a setting to change
    but a session to renew, which for SSO means running ``aws sso login`` again.
    """

    status_code = 503


class ConfigurationError(AppError):
    """A required setting is missing, e.g. no X token when the X source is requested."""

    status_code = 503


class ConflictError(AppError):
    """A competing dataset edit must finish before this operation can start."""

    status_code = 409
