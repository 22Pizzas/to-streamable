"""Errors the CLI prints, each with a stable exit code."""


class StreamableError(Exception):
    """Base error. Subclasses set exit_code."""

    exit_code = 1


class UsageError(StreamableError):
    exit_code = 2


class AuthError(StreamableError):
    """Missing credentials, or Streamable rejected them."""

    exit_code = 3


class ApiChangedError(StreamableError):
    """Response does not match the unofficial upload contract."""

    exit_code = 4


class NetworkError(StreamableError):
    exit_code = 5


class RejectedError(StreamableError):
    """Streamable accepted the connection but refused the upload."""

    exit_code = 6


class ProcessingError(StreamableError):
    """Upload was accepted, then processing failed or timed out."""

    exit_code = 7
