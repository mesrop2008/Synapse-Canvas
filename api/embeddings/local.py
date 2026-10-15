"""paraphrase-multilingual-MiniLM-L12-v2 on this machine's CPU, through
sentence-transformers: English and Russian, no key, no network after the
first download. Install it with requirements-local.txt."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from api.embeddings.base import (
    EmbeddingBatch,
    EmbeddingKind,
    EmbeddingProvider,
    EmbeddingUsage,
)
from api.embeddings.errors import EmbeddingFailedError

LOCAL_EMBEDDING_DIMENSION = 384
# The model's position embeddings stop here. sentence-transformers would cut
# anything longer at its default of 128 without a word, so the limit is set
# explicitly and enforced before encoding instead.
LOCAL_EMBEDDING_INPUT_LIMIT = 512


class LocalEmbeddingProvider(EmbeddingProvider):
    name = "local"
    dimension = LOCAL_EMBEDDING_DIMENSION
    max_input_tokens = LOCAL_EMBEDDING_INPUT_LIMIT
    default_min_similarity = 0.35

    def __init__(self, *, model: str, device: str = "cpu") -> None:
        self.model = model
        self._device = device
        self._loaded: Any = None
        self._lock = threading.Lock()

    def _encoder(self) -> Any:
        # Loaded on first use: importing torch and reading the weights takes
        # seconds, and the API process never embeds a document.
        with self._lock:
            if self._loaded is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:
                    raise EmbeddingFailedError(
                        "EMBEDDING_PROVIDER=local needs sentence-transformers; "
                        "install requirements-local.txt"
                    ) from exc
                encoder = SentenceTransformer(self.model, device=self._device)
                encoder.max_seq_length = LOCAL_EMBEDDING_INPUT_LIMIT
                self._loaded = encoder
            return self._loaded

    def count_tokens(self, text: str) -> int:
        """Exact: the model's own tokenizer, special tokens included."""
        tokenizer = self._encoder().tokenizer
        encoded = tokenizer(text, add_special_tokens=True, truncation=False, verbose=False)
        return len(encoded["input_ids"])

    async def _embed(self, texts: list[str], kind: EmbeddingKind) -> EmbeddingBatch:
        def encode() -> list[list[float]]:
            vectors = self._encoder().encode(
                texts, normalize_embeddings=True, convert_to_numpy=True
            )
            return [[float(value) for value in vector] for vector in vectors]

        try:
            vectors = await asyncio.to_thread(encode)
        except EmbeddingFailedError:
            raise
        except Exception as exc:
            raise EmbeddingFailedError("The local embedding model failed") from exc
        return EmbeddingBatch(
            vectors=vectors,
            usage=EmbeddingUsage(
                requests=1,
                texts=len(texts),
                tokens=sum(self.count_tokens(text) for text in texts),
                model=self.model,
            ),
        )
