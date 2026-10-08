from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Sequence

from api.core import i18n
from api.llm.base import LLMProvider, LLMRequest, Usage, estimate_tokens
from api.llm.errors import ProviderError


def split_words(text: str) -> list[str]:
    return re.findall(r"\S+\s*", text)


class FakeProvider(LLMProvider):
    """Scripted chunks, each after `delay` seconds; without a script, a reply
    from the language packs that explains itself. `fail_with` is raised in
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
        self.chunks = list(chunks) if chunks is not None else None
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
        chunks = self.chunks
        if chunks is None:
            chunks = split_words(i18n.text(request.locale, "ai.fake.reply"))
        try:
            for index, chunk in enumerate(chunks):
                if self.fail_with is not None and index == self.fail_after:
                    raise self.fail_with
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield chunk
            if self.fail_with is not None and self.fail_after >= len(chunks):
                raise self.fail_with

            self.finished += 1
            yield Usage(
                prompt_tokens=estimate_tokens(request.system + request.user),
                completion_tokens=estimate_tokens("".join(chunks)),
                model=self.model,
            )
        except (GeneratorExit, asyncio.CancelledError):
            self.aborted += 1
            raise
