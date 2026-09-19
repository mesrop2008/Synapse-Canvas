"""Services raise these instead of HTTP errors; one handler in `api.main` maps
them to responses, which keeps the service layer FastAPI-free."""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable identifiers for client-facing failures.

    The client maps these to its own language packs, so translations live with
    the client and adding a language changes nothing here. `detail` stays
    English alongside them: it is what a non-browser caller reads, and what a
    client that has not learned a new code yet falls back to.

    The values are part of the API contract. Renaming one silently drops every
    released client back to the English sentence.
    """

    # Generic
    SERVER_ERROR = "server.error"
    REQUEST_INVALID = "request.invalid"
    REQUEST_TOO_LARGE = "request.too_large"
    REQUEST_MALFORMED_LENGTH = "request.malformed_length"
    RATE_LIMITED = "rate_limit.exceeded"

    # Authentication and tokens
    NOT_AUTHENTICATED = "auth.not_authenticated"
    INVALID_TOKEN = "auth.invalid_token"
    TOKEN_EXPIRED = "auth.token_expired"
    WRONG_TOKEN_TYPE = "auth.wrong_token_type"
    INVALID_CREDENTIALS = "auth.invalid_credentials"
    USER_GONE = "auth.user_gone"
    EMAIL_UNVERIFIED = "auth.email_unverified"
    VERIFICATION_INVALID = "auth.verification_invalid"
    REFRESH_UNKNOWN = "auth.refresh_unknown"
    REFRESH_REVOKED = "auth.refresh_revoked"
    REFRESH_EXPIRED = "auth.refresh_expired"

    # Workspaces and membership
    WORKSPACE_NOT_FOUND = "workspace.not_found"
    ROLE_TOO_LOW = "workspace.role_too_low"
    MEMBER_UNKNOWN_EMAIL = "member.unknown_email"
    MEMBER_UNVERIFIED = "member.unverified"
    MEMBER_DUPLICATE = "member.duplicate"
    MEMBER_ABSENT = "member.absent"
    MEMBER_IS_OWNER = "member.is_owner"

    # Documents
    DOCUMENT_NOT_FOUND = "document.not_found"
    DOCUMENT_STALE = "document.stale"


class AppError(Exception):
    """Base class for expected, client-facing failures."""

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
        # Keyword-only, so a raise site that does not care gets its exception
        # class's default rather than accidentally passing headers as a code.
        self.code = code or self.__class__.code
        self.headers = headers  # e.g. Retry-After on a 429
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


class EmailNotVerifiedError(AppError):
    # Raised only after the password verifies, so it is not an enumeration
    # signal.
    status_code = 403
    detail = "Email address has not been verified"
    code = ErrorCode.EMAIL_UNVERIFIED
