"""Embedding providers: the fake the suite runs on, the input limit every
provider enforces before sending, and Gemini's request and error mapping on
the real SDK against a mock transport."""

from __future__ import annotations

import json
import math
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from api.core.config import Settings
from api.embeddings.base import (
    EmbeddingBatch,
    EmbeddingUsage,
    conservative_token_count,
)
from api.embeddings.errors import (
    EmbeddingFailedError,
    InputTooLongError,
    InvalidKeyError,
    RateLimitedError,
    UpstreamUnavailableError,
)
from api.embeddings.fake import FakeEmbeddingProvider
from api.embeddings.gemini import GeminiEmbeddingProvider


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


# --- Fake ---


async def test_fake_vectors_are_deterministic_unit_length_and_word_based() -> None:
    provider = FakeEmbeddingProvider(dimension=64)
    texts = [
        "Photosynthesis turns light into chemical energy.",
        "Photosynthesis turns light into chemical energy.",
        "Chlorophyll absorbs light for photosynthesis.",
        "The match ended two goals to one.",
    ]

    batch = await provider.embed(texts, "document")

    first, same, related, unrelated = batch.vectors
    assert first == same
    assert math.isclose(math.sqrt(cosine(first, first)), 1.0)
    assert cosine(first, related) > cosine(first, unrelated)
    assert batch.usage == EmbeddingUsage(
        requests=1,
        texts=4,
        tokens=sum(conservative_token_count(text) for text in texts),
        model="fake-embedding-1",
    )
    assert provider.calls == [texts]


async def test_a_text_over_the_input_limit_is_refused_before_any_request() -> None:
    provider = FakeEmbeddingProvider(max_input_tokens=50)
    texts = ["A short passage.", "word " * 200]

    with pytest.raises(InputTooLongError) as raised:
        await provider.embed(texts, "document")

    assert raised.value.index == 1
    assert raised.value.limit == 50
    assert raised.value.tokens > 50
    assert provider.calls == []


async def test_vectors_of_the_wrong_size_are_refused() -> None:
    class Truncating(FakeEmbeddingProvider):
        async def _embed(self, texts: list[str], kind: Any) -> EmbeddingBatch:
            batch = await super()._embed(texts, kind)
            return EmbeddingBatch([v[:-1] for v in batch.vectors], batch.usage)

    with pytest.raises(EmbeddingFailedError):
        await Truncating(dimension=8).embed(["text"], "query")


def test_the_token_estimate_over_counts_digits_and_other_scripts() -> None:
    assert conservative_token_count("") == 0
    assert conservative_token_count("cat") == 1
    assert conservative_token_count("Москва") == 2
    # Tokenizers split digits one by one.
    assert conservative_token_count("2026") == 4
    assert conservative_token_count("3.14!") == 5
    assert conservative_token_count("東京都") == 3
    assert conservative_token_count("one two  three\n\nfour") == 6


# --- Gemini, against a mock transport ---


class EmbedStub:
    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body
        self.requests: list[dict[str, Any]] = []

    def provider(
        self, api_key: str = "test-key", dimension: int = 4
    ) -> GeminiEmbeddingProvider:
        return GeminiEmbeddingProvider(
            api_key=api_key,
            model="gemini-embedding-001",
            dimension=dimension,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self._handle)),
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.requests.append({"url": str(request.url), "json": payload})
        if self.body is not None:
            return httpx.Response(self.status, json=self.body)
        count = len(payload["requests"])
        return httpx.Response(
            200, json={"embeddings": [{"values": [3.0, 4.0, 0.0, 0.0]}] * count}
        )


async def test_gemini_batches_texts_into_one_request_and_normalizes() -> None:
    stub = EmbedStub()

    batch = await stub.provider().embed(["first", "second"], "document")

    assert len(stub.requests) == 1
    request = stub.requests[0]
    assert request["url"].endswith("models/gemini-embedding-001:batchEmbedContents")
    sent = request["json"]["requests"]
    assert [item["content"]["parts"][0]["text"] for item in sent] == ["first", "second"]
    assert {item["taskType"] for item in sent} == {"RETRIEVAL_DOCUMENT"}
    assert {item["outputDimensionality"] for item in sent} == {4}
    assert batch.vectors == [[0.6, 0.8, 0.0, 0.0]] * 2
    assert batch.usage.requests == 1 and batch.usage.texts == 2


async def test_gemini_embeds_queries_as_queries() -> None:
    stub = EmbedStub()

    await stub.provider().embed(["what is it?"], "query")

    assert stub.requests[0]["json"]["requests"][0]["taskType"] == "RETRIEVAL_QUERY"


async def test_gemini_checks_the_input_limit_before_sending() -> None:
    stub = EmbedStub()

    with pytest.raises(InputTooLongError):
        await stub.provider().embed(["fine", "9" * 3000], "document")

    assert stub.requests == []


def quota_error(quota_id: str) -> dict[str, Any]:
    return {
        "error": {
            "code": 429,
            "message": "Resource has been exhausted (e.g. check quota).",
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [{"quotaId": quota_id}],
                },
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "17s",
                },
            ],
        }
    }


@pytest.mark.parametrize(
    ("quota_id", "daily"),
    [
        ("EmbedContentRequestsPerMinutePerProjectPerModel-FreeTier", False),
        ("EmbedContentRequestsPerDayPerProjectPerModel-FreeTier", True),
    ],
)
async def test_a_429_is_rate_limited_and_says_which_window(
    quota_id: str, daily: bool
) -> None:
    stub = EmbedStub(429, quota_error(quota_id))

    with pytest.raises(RateLimitedError) as raised:
        await stub.provider().embed(["text"], "document")

    assert raised.value.daily is daily
    assert raised.value.retry_after == 17.0
    assert raised.value.code.value == "embedding.quota_exhausted"


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (400, InvalidKeyError),
        (503, UpstreamUnavailableError),
        (500, UpstreamUnavailableError),
        (404, EmbeddingFailedError),
    ],
)
async def test_gemini_errors_map_to_embedding_errors(status: int, error: type) -> None:
    message = "API key not valid." if status == 400 else "nope"
    stub = EmbedStub(status, {"error": {"code": status, "message": message}})

    with pytest.raises(error):
        await stub.provider().embed(["text"], "document")


async def test_gemini_without_a_key_fails_without_a_request() -> None:
    stub = EmbedStub()
    provider = stub.provider(api_key="")

    assert not provider.configured
    with pytest.raises(InvalidKeyError):
        await provider.embed(["text"], "document")
    assert stub.requests == []


# --- Settings ---


def settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "jwt_secret_key": "x" * 40,
        "environment": "test",
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def test_a_dimension_pgvector_cannot_index_is_refused() -> None:
    with pytest.raises(ValidationError, match="at most 2000"):
        settings(embedding_provider="gemini", embedding_dimension=3072)


def test_the_local_model_needs_its_own_dimension() -> None:
    with pytest.raises(ValidationError, match="EMBEDDING_DIMENSION=384"):
        settings(embedding_provider="local", embedding_dimension=1536)

    assert settings(embedding_provider="local", embedding_dimension=384)


def test_the_fake_is_refused_outside_local_and_test() -> None:
    with pytest.raises(ValidationError, match="EMBEDDING_PROVIDER=fake"):
        settings(
            embedding_provider="fake",
            environment="production",
            llm_provider="gemini",
            email_backend="smtp",
            smtp_host="smtp.example.com",
            mail_from_address="a@example.com",
        )
