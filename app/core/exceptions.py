"""Domain-level exceptions.

The service layer must not import FastAPI or know about HTTP. It raises these
instead, and a single handler registered in `app.main` maps them onto
responses. That keeps `routers/` the only layer aware of the transport, and
means services stay directly reusable from the WebSocket and background-worker
entry points planned for later parts.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for expected, client-facing failures."""

    status_code: int = 500
    detail: str = "Internal server error"

    def __init__(
        self, detail: str | None = None, headers: dict[str, str] | None = None
    ) -> None:
        self.detail = detail or self.__class__.detail
        # Some failures are only actionable with a header attached -- a 429 is
        # not much use to a client without Retry-After.
        self.headers = headers
        super().__init__(self.detail)


class AuthenticationError(AppError):
    status_code = 401
    detail = "Could not validate credentials"


class PermissionDeniedError(AppError):
    status_code = 403
    detail = "Insufficient permissions"


class NotFoundError(AppError):
    status_code = 404
    detail = "Resource not found"


class ConflictError(AppError):
    status_code = 409
    detail = "Resource already exists"


class RateLimitExceededError(AppError):
    status_code = 429
    detail = "Too many requests. Please try again later."

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            self.__class__.detail,
            headers={"Retry-After": str(retry_after_seconds)},
        )
