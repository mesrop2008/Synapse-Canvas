"""GeminiProvider on the real SDK, against a mock transport: no key, no
network. Error mapping, and whether cancellation reaches the HTTP response."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from api.llm.base import LLMRequest, Usage
from api.llm.errors import (
    ContentFilteredError,
    ContextTooLongError,
    InvalidKeyError,
    ProviderError,
    RateLimitedError,
    UpstreamUnavailableError,
)
from api.llm.gemini import GeminiProvider

REQUEST = LLMRequest(
    system="Be brief.", user="<document>x</document>", max_output_tokens=64
)


def chunk(text: str, **extra: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {"content": {"role": "model", "parts": [{"text": text}]}}
    candidate.update(extra.pop("candidate", {}))
    return {"candidates": [candidate], **extra}


class GeminiStub:
    """Answers like Gemini's streamGenerateContent, and counts the response
    bodies that were closed before they finished."""

    def __init__(
        self,
        bodies: list[dict[str, Any]] | None = None,
        *,
        delay: float = 0.0,
        status: int = 200,
        error: dict[str, Any] | None = None,
        raises: Exception | None = None,
    ) -> None:
        default = [chunk(f"w{i} ") for i in range(3)]
        self.bodies = default if bodies is None else bodies
        self.delay = delay
        self.status = status
        self.error = error
        self.raises = raises
        self.requests: list[httpx.Request] = []
        self.served = 0
        self.closed_early = 0

    def provider(self, api_key: str = "test-key") -> GeminiProvider:
        transport = httpx.MockTransport(self._handle)
        return GeminiProvider(
            api_key=api_key,
            model="gemini-test",
            http_client=httpx.AsyncClient(transport=transport),
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        if self.error is not None:
            return httpx.Response(self.status, json=self.error)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_Body(self),
        )


class _Body(httpx.AsyncByteStream):
    def __init__(self, stub: GeminiStub) -> None:
        self.stub = stub
        self.finished = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for body in self.stub.bodies:
            if self.stub.delay:
                await asyncio.sleep(self.stub.delay)
            self.stub.served += 1
            yield f"data: {json.dumps(body)}\r\n\r\n".encode()
        self.finished = True

    async def aclose(self) -> None:
        if not self.finished:
            self.stub.closed_early += 1


async def collect(provider: GeminiProvider) -> list[str | Usage]:
    return [item async for item in provider.stream(REQUEST)]


async def test_text_streams_and_usage_comes_from_the_final_chunk() -> None:
    stub = GeminiStub(
        [
            chunk("Hello "),
            chunk(
                "world.",
                candidate={"finishReason": "STOP"},
                usageMetadata={
                    "promptTokenCount": 12,
                    "candidatesTokenCount": 3,
                    "thoughtsTokenCount": 4,
                },
            ),
        ]
    )

    items = await collect(stub.provider())

    assert items == [
        "Hello ",
        "world.",
        Usage(prompt_tokens=12, completion_tokens=7, model="gemini-test"),
    ]


async def test_the_key_travels_in_a_header_and_the_prompt_in_the_body() -> None:
    stub = GeminiStub()
    await collect(stub.provider("test-key"))

    [request] = stub.requests
    assert request.headers["x-goog-api-key"] == "test-key"
    assert "test-key" not in str(request.url)
    body = json.loads(request.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "Be brief."
    # The SDK has sent this key both snake- and camel-cased; the API takes either.
    assert list(body["generationConfig"]["thinkingConfig"].values()) == [0]


async def test_thought_parts_are_not_streamed_as_text() -> None:
    stub = GeminiStub(
        [
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Let me think", "thought": True},
                                {"text": "Answer."},
                            ]
                        }
                    }
                ]
            }
        ]
    )

    assert (await collect(stub.provider()))[0] == "Answer."


def _error(code: int, status: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "status": status, "message": message}}


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        (429, _error(429, "RESOURCE_EXHAUSTED", "Quota exceeded"), RateLimitedError),
        (
            400,
            _error(400, "INVALID_ARGUMENT", "API key not valid. Pass a valid API key."),
            InvalidKeyError,
        ),
        (403, _error(403, "PERMISSION_DENIED", "Permission denied"), InvalidKeyError),
        (
            400,
            _error(
                400,
                "INVALID_ARGUMENT",
                "The input token count (2000000) exceeds the maximum number of "
                "tokens allowed (1048576).",
            ),
            ContextTooLongError,
        ),
        (503, _error(503, "UNAVAILABLE", "Overloaded"), UpstreamUnavailableError),
        (500, _error(500, "INTERNAL", "Internal error"), UpstreamUnavailableError),
        (400, _error(400, "INVALID_ARGUMENT", "Something else"), ProviderError),
    ],
)
async def test_sdk_errors_become_our_own(
    status: int, error: dict[str, Any], expected: type[ProviderError]
) -> None:
    stub = GeminiStub(status=status, error=error)

    with pytest.raises(expected) as raised:
        await collect(stub.provider())

    assert type(raised.value) is expected
    # Our wording, never the provider's.
    assert error["error"]["message"] not in str(raised.value)


@pytest.mark.parametrize(
    "body",
    [
        {"promptFeedback": {"blockReason": "SAFETY"}},
        chunk("", candidate={"finishReason": "SAFETY"}),
        chunk("Quoted", candidate={"finishReason": "RECITATION"}),
    ],
)
async def test_blocked_content_is_reported_as_filtered(body: dict[str, Any]) -> None:
    with pytest.raises(ContentFilteredError):
        await collect(GeminiStub([body]).provider())


@pytest.mark.parametrize(
    "failure", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")]
)
async def test_an_unreachable_provider_is_upstream_unavailable(
    failure: Exception,
) -> None:
    with pytest.raises(UpstreamUnavailableError):
        await collect(GeminiStub(raises=failure).provider())


async def test_without_a_key_nothing_is_sent() -> None:
    stub = GeminiStub()
    provider = stub.provider(api_key="")

    assert not provider.configured
    with pytest.raises(InvalidKeyError):
        await collect(provider)
    assert stub.requests == []


async def test_cancelling_the_consumer_closes_the_http_response() -> None:
    """What the runner relies on: cancelling the task awaiting the next chunk
    unwinds through the SDK and closes the response it is reading."""
    stub = GeminiStub([chunk(f"w{i} ") for i in range(100)], delay=0.02)
    received: list[str | Usage] = []
    flowing = asyncio.Event()

    async def consume() -> None:
        async for item in stub.provider().stream(REQUEST):
            received.append(item)
            if len(received) == 3:
                flowing.set()

    task = asyncio.create_task(consume())
    await asyncio.wait_for(flowing.wait(), timeout=5)
    task.cancel()
    await asyncio.wait({task})

    assert stub.closed_early == 1
    assert stub.served < 100
