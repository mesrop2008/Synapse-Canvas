"""gemini-embedding-001 through the google-genai SDK, on the same key as the
LLM. The SDK does not retry unless asked to; the ingestion service does, so
that each attempt is counted against the workspace."""

from __future__ import annotations

import json
import logging
import re

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from api.embeddings.base import (
    EmbeddingBatch,
    EmbeddingKind,
    EmbeddingProvider,
    EmbeddingUsage,
    normalized,
)
from api.embeddings.errors import (
    EmbeddingError,
    EmbeddingFailedError,
    InvalidKeyError,
    RateLimitedError,
    UpstreamUnavailableError,
)

logger = logging.getLogger(__name__)

# Per text, per the model card. Longer input is truncated without an error.
GEMINI_EMBEDDING_INPUT_LIMIT = 2048

_TASK_TYPES: dict[EmbeddingKind, str] = {
    "document": "RETRIEVAL_DOCUMENT",
    "query": "RETRIEVAL_QUERY",
}
_RETRY_DELAY = re.compile(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"')


class GeminiEmbeddingProvider(EmbeddingProvider):
    name = "gemini"
    max_input_tokens = GEMINI_EMBEDDING_INPUT_LIMIT
    metered = True
    # Gemini scores unrelated text 0.4 to 0.5 against a question, related
    # text from about 0.65.
    default_min_similarity = 0.6

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimension: int,
        timeout_seconds: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self.dimension = dimension
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

    async def _embed(self, texts: list[str], kind: EmbeddingKind) -> EmbeddingBatch:
        if self._client is None:
            raise InvalidKeyError("GEMINI_API_KEY is not set")

        config = types.EmbedContentConfig(
            task_type=_TASK_TYPES[kind], output_dimensionality=self.dimension
        )
        try:
            response = await self._client.aio.models.embed_content(
                model=self.model, contents=texts, config=config
            )
        except genai_errors.APIError as exc:
            raise _map_api_error(exc) from None
        except httpx.TimeoutException:
            raise UpstreamUnavailableError("The embedding provider timed out") from None
        except httpx.TransportError:
            raise UpstreamUnavailableError() from None

        embeddings = response.embeddings or []
        if any(not embedding.values for embedding in embeddings):
            raise EmbeddingFailedError("The embedding provider returned an empty vector")
        reported = [
            embedding.statistics.token_count
            for embedding in embeddings
            if embedding.statistics and embedding.statistics.token_count is not None
        ]
        tokens = (
            int(sum(reported))
            if len(reported) == len(texts)
            else sum(self.count_tokens(text) for text in texts)
        )
        return EmbeddingBatch(
            # Only the full 3072 dimensions come back normalized.
            vectors=[normalized(embedding.values or []) for embedding in embeddings],
            usage=EmbeddingUsage(
                requests=1, texts=len(texts), tokens=tokens, model=self.model
            ),
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aio.aclose()


def _map_api_error(exc: genai_errors.APIError) -> EmbeddingError:
    logger.warning("Gemini refused the embedding request: %s %s", exc.code, exc.status)
    code = exc.code or 0
    message = (exc.message or "").lower()

    if code == 429 or exc.status == "RESOURCE_EXHAUSTED":
        # The quota violation names its window, e.g. "...PerDayPerProject...".
        details = json.dumps(exc.details, default=str)
        delay = _RETRY_DELAY.search(details)
        return RateLimitedError(
            daily="PerDay" in details,
            retry_after=float(delay.group(1)) if delay else None,
        )
    if code in (401, 403) or "api key" in message or "api_key" in message:
        return InvalidKeyError()
    if code >= 500 or exc.status in ("UNAVAILABLE", "DEADLINE_EXCEEDED"):
        return UpstreamUnavailableError()
    return EmbeddingFailedError()
