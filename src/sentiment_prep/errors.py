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


class ConfigurationError(AppError):
    """A required setting is missing, e.g. no X token when the X source is requested."""

    status_code = 503
