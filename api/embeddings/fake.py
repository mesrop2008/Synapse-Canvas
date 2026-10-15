from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from api.embeddings.base import (
    EmbeddingBatch,
    EmbeddingKind,
    EmbeddingProvider,
    EmbeddingUsage,
    normalized,
)
from api.embeddings.errors import EmbeddingError

_WORD = re.compile(r"\w+")


def hashed_vector(text: str, dimension: int) -> list[float]:
    """Feature hashing: each word adds ±1 to a dimension picked by its hash.
    Deterministic, offline, and texts that share words come out similar, so
    tests can tell a related query from an unrelated one."""
    vector = [0.0] * dimension
    words = _WORD.findall(text.lower()) or [text]
    for word in words:
        digest = hashlib.sha256(word.encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % dimension
        vector[index] += 1.0 if digest[8] & 1 else -1.0
    return normalized(vector)


class FakeEmbeddingProvider(EmbeddingProvider):
    """For the test suite: fast, offline and deterministic. Not a stand-in for
    a missing key -- without one, use the local provider.

    `failures` are raised one per call, in order, before any vector is made.
    `calls` records each request's texts."""

    name = "fake"
    default_min_similarity = 0.3

    def __init__(
        self,
        *,
        dimension: int = 1536,
        model: str = "fake-embedding-1",
        max_input_tokens: int = 2048,
        metered: bool = False,
        failures: Sequence[EmbeddingError] = (),
    ) -> None:
        self.dimension = dimension
        self.model = model
        self.max_input_tokens = max_input_tokens
        self.metered = metered
        self.failures = list(failures)
        self.calls: list[list[str]] = []

    async def _embed(self, texts: list[str], kind: EmbeddingKind) -> EmbeddingBatch:
        self.calls.append(texts)
        if self.failures:
            raise self.failures.pop(0)
        return EmbeddingBatch(
            vectors=[hashed_vector(text, self.dimension) for text in texts],
            usage=EmbeddingUsage(
                requests=1,
                texts=len(texts),
                tokens=sum(self.count_tokens(text) for text in texts),
                model=self.model,
            ),
        )
