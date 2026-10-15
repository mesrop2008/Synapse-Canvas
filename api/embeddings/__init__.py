"""Embedding providers behind one interface, chosen by EMBEDDING_PROVIDER.
Nothing outside this package imports an embedding SDK."""

from __future__ import annotations

import logging

from api.core.config import Settings
from api.embeddings.base import EmbeddingProvider

logger = logging.getLogger(__name__)


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Imported lazily: a Gemini process never loads torch, and the reverse."""
    if settings.embedding_provider == "gemini":
        from api.embeddings.gemini import GeminiEmbeddingProvider

        return GeminiEmbeddingProvider(
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.gemini_embedding_model,
            dimension=settings.embedding_dimension,
            timeout_seconds=settings.embedding_timeout_seconds,
        )

    if settings.embedding_provider == "local":
        from api.embeddings.local import LocalEmbeddingProvider

        return LocalEmbeddingProvider(model=settings.local_embedding_model)

    from api.embeddings.fake import FakeEmbeddingProvider

    return FakeEmbeddingProvider(dimension=settings.embedding_dimension)


def report_status(provider: EmbeddingProvider) -> None:
    if getattr(provider, "configured", True):
        logger.info(
            "Embedding provider: %s (%s, %d dimensions)",
            provider.name,
            provider.model,
            provider.dimension,
        )
    else:
        logger.warning(
            "EMBEDDING_PROVIDER=%s but no API key is set; every upload will fail "
            "with embedding.invalid_key until GEMINI_API_KEY is configured, or "
            "set EMBEDDING_PROVIDER=local to embed on this machine.",
            provider.name,
        )
