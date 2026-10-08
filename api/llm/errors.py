"""What a provider may raise. Each maps an SDK failure onto an `ErrorCode` the
client translates, so no SDK exception class gets past `api.llm`."""

from __future__ import annotations

from api.core.exceptions import ErrorCode


class ProviderError(Exception):
    code: ErrorCode = ErrorCode.AI_FAILED
    detail: str = "The AI provider could not complete the request"

    def __init__(self, detail: str | None = None) -> None:
        # Never the SDK's own message: it can echo the prompt back.
        self.detail = detail or self.__class__.detail
        super().__init__(self.detail)


class RateLimitedError(ProviderError):
    code = ErrorCode.AI_RATE_LIMITED
    detail = "The AI provider is rate limiting requests"


class ContextTooLongError(ProviderError):
    code = ErrorCode.AI_CONTEXT_TOO_LONG
    detail = "The request is longer than the model accepts"


class ContentFilteredError(ProviderError):
    code = ErrorCode.AI_CONTENT_FILTERED
    detail = "The AI provider blocked this content"


class UpstreamUnavailableError(ProviderError):
    code = ErrorCode.AI_UPSTREAM_UNAVAILABLE
    detail = "The AI provider is unavailable"


class InvalidKeyError(ProviderError):
    code = ErrorCode.AI_INVALID_KEY
    detail = "The AI provider rejected the API key, or none is configured"
