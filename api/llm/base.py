from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

from api.core.i18n import DEFAULT_LOCALE, Locale

# A rough average across English and Cyrillic text; deliberately low, so the
# estimate errs towards more tokens.
CHARS_PER_TOKEN = 3


def estimate_tokens(text: str) -> int:
    """For budgeting before a call. The provider's usage report is what gets
    billed and recorded."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """`system` is ours; everything a user wrote travels in `user`. `locale`
    is the user's interface language, for providers that write their own
    text."""

    system: str
    user: str
    max_output_tokens: int
    locale: Locale = DEFAULT_LOCALE


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    model: str

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def stream(self, request: LLMRequest) -> AsyncIterator[str | Usage]:
        """Text chunks as they arrive, then one `Usage` as the last item.

        Closing the iterator early, or cancelling the task awaiting it, must
        abort the upstream call. Raises only `api.llm.errors.ProviderError`.
        """

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        pass
