"""Gemini through the official google-genai SDK.

On cancellation: the SDK's stream is several nested async generators, and only
the innermost closes the HTTP response, in a `finally`. Calling `aclose()` on
the outer one does not reach it; the inner generators are left to the garbage
collector. What does reach it is cancelling the task while it awaits the next
chunk: the CancelledError unwinds through every frame. The runner relies on
that, and tests/test_ai_gemini.py checks it against a mock transport."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import aclosing

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from api.llm.base import LLMProvider, LLMRequest, Usage, estimate_tokens
from api.llm.errors import (
    ContentFilteredError,
    ContextTooLongError,
    InvalidKeyError,
    ProviderError,
    RateLimitedError,
    UpstreamUnavailableError,
)

logger = logging.getLogger(__name__)

_FILTERED = {
    types.FinishReason.SAFETY,
    types.FinishReason.PROHIBITED_CONTENT,
    types.FinishReason.BLOCKLIST,
    types.FinishReason.SPII,
    types.FinishReason.RECITATION,
}


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        thinking_budget: int = 0,
        timeout_seconds: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self._thinking_budget = thinking_budget
        # No client without a key: the SDK would go looking in the environment.
        self._client: genai.Client | None = None
        if api_key:
            self._client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=int(timeout_seconds * 1000),
                    httpx_async_client=http_client,
                ),
            )

    @property
    def configured(self) -> bool:
        return self._client is not None

    async def stream(self, request: LLMRequest) -> AsyncIterator[str | Usage]:
        if self._client is None:
            raise InvalidKeyError("GEMINI_API_KEY is not set")

        config = types.GenerateContentConfig(
            system_instruction=request.system,
            max_output_tokens=request.max_output_tokens,
            thinking_config=(
                types.ThinkingConfig(thinking_budget=self._thinking_budget)
                if self._thinking_budget >= 0
                else None
            ),
        )
        metadata: types.GenerateContentResponseUsageMetadata | None = None
        produced: list[str] = []

        try:
            chunks = await self._client.aio.models.generate_content_stream(
                model=self.model, contents=request.user, config=config
            )
            async with aclosing(chunks):
                async for chunk in chunks:
                    _raise_if_blocked(chunk)
                    metadata = chunk.usage_metadata or metadata
                    text = _text_of(chunk)
                    if text:
                        produced.append(text)
                        yield text
        except genai_errors.APIError as exc:
            raise _map_api_error(exc) from None
        except httpx.TimeoutException:
            raise UpstreamUnavailableError("The AI provider timed out") from None
        except httpx.TransportError:
            raise UpstreamUnavailableError() from None

        yield _usage(metadata, request, produced, self.model)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aio.aclose()


def _text_of(chunk: types.GenerateContentResponse) -> str:
    # Not `chunk.text`, which warns on every non-text part.
    if not chunk.candidates or chunk.candidates[0].content is None:
        return ""
    parts = chunk.candidates[0].content.parts or []
    return "".join(part.text for part in parts if part.text and not part.thought)


def _raise_if_blocked(chunk: types.GenerateContentResponse) -> None:
    if chunk.prompt_feedback is not None and chunk.prompt_feedback.block_reason:
        raise ContentFilteredError("The AI provider blocked this prompt")
    if chunk.candidates and chunk.candidates[0].finish_reason in _FILTERED:
        raise ContentFilteredError()


def _usage(
    metadata: types.GenerateContentResponseUsageMetadata | None,
    request: LLMRequest,
    produced: list[str],
    model: str,
) -> Usage:
    if metadata is None or metadata.prompt_token_count is None:
        return Usage(
            prompt_tokens=estimate_tokens(request.system + request.user),
            completion_tokens=estimate_tokens("".join(produced)),
            model=model,
        )
    # Thinking tokens are billed as output.
    completion = (metadata.candidates_token_count or 0) + (
        metadata.thoughts_token_count or 0
    )
    return Usage(
        prompt_tokens=metadata.prompt_token_count,
        completion_tokens=completion,
        model=model,
    )


def _map_api_error(exc: genai_errors.APIError) -> ProviderError:
    # Status and code only: the details can quote the request.
    logger.warning("Gemini refused the request: %s %s", exc.code, exc.status)
    code = exc.code or 0
    message = (exc.message or "").lower()

    if code == 429 or exc.status == "RESOURCE_EXHAUSTED":
        return RateLimitedError()
    if code in (401, 403) or "api key" in message or "api_key" in message:
        return InvalidKeyError()
    if code == 400 and "token" in message and "exceed" in message:
        return ContextTooLongError()
    if code >= 500 or exc.status in ("UNAVAILABLE", "DEADLINE_EXCEEDED"):
        return UpstreamUnavailableError()
    return ProviderError()
