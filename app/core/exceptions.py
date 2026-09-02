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

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.__class__.detail
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
