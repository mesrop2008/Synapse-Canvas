"""Services raise these instead of HTTP errors; one handler in `api.main` maps
them to responses, which keeps the service layer FastAPI-free."""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Part of the API contract: the client translates these, and renaming one
    drops released clients back to the English `detail`."""

    SERVER_ERROR = "server.error"
    REQUEST_INVALID = "request.invalid"
    REQUEST_TOO_LARGE = "request.too_large"
    REQUEST_MALFORMED_LENGTH = "request.malformed_length"
    RATE_LIMITED = "rate_limit.exceeded"

    NOT_AUTHENTICATED = "auth.not_authenticated"
    INVALID_TOKEN = "auth.invalid_token"
    TOKEN_EXPIRED = "auth.token_expired"
    WRONG_TOKEN_TYPE = "auth.wrong_token_type"
    INVALID_CREDENTIALS = "auth.invalid_credentials"
    USER_GONE = "auth.user_gone"
    EMAIL_UNVERIFIED = "auth.email_unverified"
    EMAIL_UNDELIVERABLE = "auth.email_undeliverable"
    VERIFICATION_INVALID = "auth.verification_invalid"
    REFRESH_UNKNOWN = "auth.refresh_unknown"
    REFRESH_REVOKED = "auth.refresh_revoked"
    REFRESH_EXPIRED = "auth.refresh_expired"

    WORKSPACE_NOT_FOUND = "workspace.not_found"
    ROLE_TOO_LOW = "workspace.role_too_low"
    MEMBER_UNKNOWN_EMAIL = "member.unknown_email"
    MEMBER_UNVERIFIED = "member.unverified"
    MEMBER_DUPLICATE = "member.duplicate"
    MEMBER_ABSENT = "member.absent"
    MEMBER_IS_OWNER = "member.is_owner"

    DOCUMENT_NOT_FOUND = "document.not_found"
    DOCUMENT_STALE = "document.stale"

    AI_FAILED = "ai.failed"
    AI_RATE_LIMITED = "ai.rate_limited"
    AI_CONTEXT_TOO_LONG = "ai.context_too_long"
    AI_CONTENT_FILTERED = "ai.content_filtered"
    AI_UPSTREAM_UNAVAILABLE = "ai.upstream_unavailable"
    AI_INVALID_KEY = "ai.invalid_key"


class AppError(Exception):
    status_code: int = 500
    detail: str = "Internal server error"
    code: ErrorCode = ErrorCode.SERVER_ERROR

    def __init__(
        self,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
        *,
        code: ErrorCode | None = None,
    ) -> None:
        self.detail = detail or self.__class__.detail
        self.code = code or self.__class__.code
        self.headers = headers
        super().__init__(self.detail)


class AuthenticationError(AppError):
    status_code = 401
    detail = "Could not validate credentials"
    code = ErrorCode.INVALID_TOKEN


class PermissionDeniedError(AppError):
    status_code = 403
    detail = "Insufficient permissions"
    code = ErrorCode.ROLE_TOO_LOW


class NotFoundError(AppError):
    status_code = 404
    detail = "Resource not found"


class ConflictError(AppError):
    status_code = 409
    detail = "Resource already exists"


class RateLimitExceededError(AppError):
    status_code = 429
    detail = "Too many requests. Please try again later."
    code = ErrorCode.RATE_LIMITED

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            self.__class__.detail,
            headers={"Retry-After": str(retry_after_seconds)},
        )


class EmailUndeliverableError(AppError):
    status_code = 422
    detail = "That email domain does not exist or does not accept mail"
    code = ErrorCode.EMAIL_UNDELIVERABLE


class EmailNotVerifiedError(AppError):
    # Raised only after the password verifies, so it reveals no account.
    status_code = 403
    detail = "Email address has not been verified"
    code = ErrorCode.EMAIL_UNVERIFIED
