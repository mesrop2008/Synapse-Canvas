"""Domain-level exceptions.

Services raise these instead of HTTP errors; a single handler in `api.main`
maps them to responses, so the service layer stays FastAPI-free and reusable.
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
        self.headers = headers  # e.g. Retry-After on a 429
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


class EmailNotVerifiedError(AppError):
    # Raised only after the password verifies, so it is not an enumeration signal.
    status_code = 403
    detail = "Email address has not been verified"
