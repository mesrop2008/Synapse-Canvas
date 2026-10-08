from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Sequence

from api.llm.base import LLMProvider, LLMRequest, Usage, estimate_tokens
from api.llm.errors import ProviderError

DEMO_REPLY = (
    "This reply comes from FakeProvider, a stand-in that streams scripted text "
    "so the assistant can be tried without an API key or network access. Set "
    "LLM_PROVIDER=gemini and GEMINI_API_KEY to get real answers about this "
    "document."
)


def split_words(text: str) -> list[str]:
    return re.findall(r"\S+\s*", text)


class FakeProvider(LLMProvider):
    """Scripted chunks, each after `delay` seconds. `fail_with` is raised in
    place of chunk number `fail_after`.

    The counters let a test tell a generation that was abandoned upstream
    (`aborted`) from one that merely stopped being read."""

    name = "fake"

    def __init__(
        self,
        chunks: Sequence[str] | None = None,
        *,
        delay: float = 0.0,
        fail_with: ProviderError | None = None,
        fail_after: int = 0,
        model: str = "fake-1",
    ) -> None:
        self.chunks = list(chunks) if chunks is not None else split_words(DEMO_REPLY)
        self.delay = delay
        self.fail_with = fail_with
        self.fail_after = fail_after
        self.model = model
        self.started = 0
        self.finished = 0
        self.aborted = 0
        self.requests: list[LLMRequest] = []

    async def stream(self, request: LLMRequest) -> AsyncIterator[str | Usage]:
        self.started += 1
        self.requests.append(request)
        try:
            for index, chunk in enumerate(self.chunks):
                if self.fail_with is not None and index == self.fail_after:
                    raise self.fail_with
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield chunk
            if self.fail_with is not None and self.fail_after >= len(self.chunks):
                raise self.fail_with

            self.finished += 1
            yield Usage(
                prompt_tokens=estimate_tokens(request.system + request.user),
                completion_tokens=estimate_tokens("".join(self.chunks)),
                model=self.model,
            )
        except (GeneratorExit, asyncio.CancelledError):
            self.aborted += 1
            raise
