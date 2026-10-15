from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from api.embeddings.errors import EmbeddingFailedError, InputTooLongError

# pgvector 0.8.7 indexes `vector` columns of at most 2,000 dimensions with HNSW.
HNSW_MAX_DIMENSIONS = 2000

# "document" for chunks being stored, "query" for a question searching them;
# providers with asymmetric retrieval embed the two differently.
EmbeddingKind = Literal["document", "query"]

# Latin, Greek and Cyrillic: about four letters per token in practice.
_ALPHABETIC_WORD = re.compile(r"[A-Za-zÀ-ɏͰ-ԯ]+")
_SPACE = re.compile(r"\s+")


def conservative_token_count(text: str) -> int:
    """An over-count, for providers whose tokenizer is not available offline.

    Words in the scripts above count one token per three letters, rounded up.
    Every other visible character -- a digit, a symbol, a letter of any other
    script -- counts as a token of its own: tokenizers split digits one by one,
    and CJK runs about a character per token."""
    words = _ALPHABETIC_WORD.findall(text)
    tokens = sum(-(-len(word) // 3) for word in words)
    rest = _SPACE.sub("", _ALPHABETIC_WORD.sub("", text))
    return tokens + len(rest)


@dataclass(frozen=True, slots=True)
class EmbeddingUsage:
    """One provider call. `tokens` is the provider's count where it reports
    one, otherwise the provider's own estimate."""

    requests: int
    texts: int
    tokens: int
    model: str


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    usage: EmbeddingUsage


class EmbeddingProvider(ABC):
    name: str
    model: str
    dimension: int
    max_input_tokens: int
    # Spends a quota someone pays for or that runs out; local models do not.
    metered: bool = False
    # Cosine similarity below which a chunk is taken as unrelated. Each model
    # spreads scores differently, so this is the model's, not the app's.
    default_min_similarity: float = 0.5

    def count_tokens(self, text: str) -> int:
        return conservative_token_count(text)

    def check_inputs(self, texts: Sequence[str]) -> None:
        for index, text in enumerate(texts):
            tokens = self.count_tokens(text)
            if tokens > self.max_input_tokens:
                raise InputTooLongError(index, tokens, self.max_input_tokens)

    async def embed(self, texts: Sequence[str], kind: EmbeddingKind) -> EmbeddingBatch:
        """Exactly one upstream request for the whole list.

        Every text is checked against the input limit first, so nothing is
        sent when one would be truncated. Raises only `EmbeddingError`."""
        if not texts:
            raise ValueError("Nothing to embed")
        self.check_inputs(texts)
        batch = await self._embed(list(texts), kind)
        if len(batch.vectors) != len(texts) or any(
            len(vector) != self.dimension for vector in batch.vectors
        ):
            raise EmbeddingFailedError(
                "The embedding provider returned vectors of the wrong number or size"
            )
        return batch

    @abstractmethod
    async def _embed(self, texts: list[str], kind: EmbeddingKind) -> EmbeddingBatch:
        """One request; `texts` is already checked."""

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        pass


def normalized(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return list(vector)
    return [value / norm for value in vector]
