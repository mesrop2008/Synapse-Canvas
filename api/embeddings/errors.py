"""What an embedding provider may raise. As in `api.llm.errors`, each maps an
SDK failure onto an `ErrorCode`, so no SDK exception gets past the package."""

from __future__ import annotations

from api.core.exceptions import ErrorCode


class EmbeddingError(Exception):
    code: ErrorCode = ErrorCode.EMBEDDING_FAILED
    detail: str = "The embedding provider could not complete the request"
    # Worth trying again soon: the same request may well succeed.
    transient: bool = False

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.__class__.detail
        super().__init__(self.detail)


class EmbeddingFailedError(EmbeddingError):
    pass


class RateLimitedError(EmbeddingError):
    """A 429. The caller backs off and tries again a few times, unless the
    provider said the quota is a daily one, then gives up with this code."""

    code = ErrorCode.EMBEDDING_QUOTA_EXHAUSTED
    detail = "The embedding provider's request quota is used up"
    transient = True

    def __init__(
        self,
        detail: str | None = None,
        *,
        daily: bool = False,
        retry_after: float | None = None,
    ) -> None:
        self.daily = daily
        self.retry_after = retry_after
        super().__init__(detail)


class InputTooLongError(EmbeddingError):
    code = ErrorCode.EMBEDDING_INPUT_TOO_LONG

    def __init__(self, index: int, tokens: int, limit: int) -> None:
        self.index = index
        self.tokens = tokens
        self.limit = limit
        super().__init__(
            f"Text {index} has about {tokens} tokens; the embedding model takes "
            f"at most {limit}. It was not sent, rather than be truncated."
        )


class InvalidKeyError(EmbeddingError):
    code = ErrorCode.EMBEDDING_INVALID_KEY
    detail = "The embedding provider rejected the API key, or none is configured"


class UpstreamUnavailableError(EmbeddingError):
    code = ErrorCode.EMBEDDING_UNAVAILABLE
    detail = "The embedding provider is unavailable"
    transient = True
