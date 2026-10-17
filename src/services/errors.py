class AdminError(Exception):
    """A refusal the operator can act on. Rendered as a message, not a stack trace."""


class PlatformError(Exception):
    """A /v1 refusal: message and HTTP status, returned as `{"detail": message}`."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
