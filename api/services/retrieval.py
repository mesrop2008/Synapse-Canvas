"""Finds the passages of a workspace's sources closest to a question.

The workspace filter is part of the SQL query that reads the vectors, so no
passage of another workspace is ever loaded, let alone ranked. pgvector
applies a filter after scanning its approximate index; iterative scans
keep reading the index until enough rows pass it, so a small workspace in
a large table still gets its own nearest passages."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.exceptions import AppError, ErrorCode
from api.embeddings.base import EmbeddingProvider
from api.models.enums import SourceStatus
from api.models.source import Source
from api.models.source_chunk import SourceChunk
from api.services import embedding_usage

_WORD = re.compile(r"\w+")
# Word overlap above which two passages of one source say the same thing:
# a running header, or a paragraph repeated on another page.
_NEAR_IDENTICAL = 0.85
# Rows read before the floor, deduplication and the budget thin them out.
_CANDIDATES_PER_RESULT = 4


class MixedEmbeddingModelsError(AppError):
    status_code = 409
    detail = (
        "This workspace holds passages embedded by more than one model; their "
        "vectors cannot be compared. Re-embed its sources with one model."
    )
    code = ErrorCode.RETRIEVAL_MIXED_MODELS


class EmbeddingModelMismatchError(AppError):
    status_code = 409
    detail = (
        "This workspace's passages were embedded by a different model from the "
        "one configured now. Re-embed its sources before searching them."
    )
    code = ErrorCode.RETRIEVAL_MODEL_MISMATCH


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    filename: str
    content: str
    page_number: int | None
    char_start: int
    char_end: int
    token_count: int
    similarity: float


@dataclass(frozen=True, slots=True)
class Retrieval:
    chunks: list[RetrievedChunk]
    min_similarity: float
    # False when there was nothing to search, so no query was embedded.
    searched: bool


def _words(content: str) -> set[str]:
    return set(_WORD.findall(content.lower()))


def _near_identical(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return a == b
    return len(a & b) / len(a | b) >= _NEAR_IDENTICAL


async def _searchable_model(
    db: AsyncSession, workspace_id: uuid.UUID, provider: EmbeddingProvider
) -> bool:
    """Whether there is anything to search, after making sure it was
    embedded by the provider's model. Only ready sources count: a failed
    one's leftover passages are never searched."""
    models = (
        await db.execute(
            select(SourceChunk.embedding_model, SourceChunk.embedding_dimension)
            .join(Source, Source.id == SourceChunk.source_id)
            .where(
                SourceChunk.workspace_id == workspace_id,
                Source.workspace_id == workspace_id,
                Source.status == SourceStatus.READY,
            )
            .distinct()
        )
    ).all()
    if not models:
        return False
    if len(models) > 1:
        raise MixedEmbeddingModelsError()
    if tuple(models[0]) != (provider.model, provider.dimension):
        raise EmbeddingModelMismatchError()
    return True


async def retrieve(
    db: AsyncSession,
    provider: EmbeddingProvider,
    workspace_id: uuid.UUID,
    query: str,
    *,
    top_k: int | None = None,
) -> Retrieval:
    """Best first: above the similarity floor, one of any near-identical
    passages of a source, and no more than the token budget in all."""
    settings = get_settings()
    top_k = top_k or settings.retrieval_top_k
    floor = settings.retrieval_min_similarity
    if floor is None:
        floor = provider.default_min_similarity

    if not await _searchable_model(db, workspace_id, provider):
        return Retrieval([], floor, searched=False)

    embedded = await embedding_usage.embed(
        db, provider, [query], kind="query", workspace_id=workspace_id
    )
    vector = embedded.vectors[0]

    distance = SourceChunk.embedding.cosine_distance(vector)
    nearest = (
        select(
            SourceChunk.id,
            SourceChunk.source_id,
            Source.filename,
            SourceChunk.content,
            SourceChunk.page_number,
            SourceChunk.char_start,
            SourceChunk.char_end,
            SourceChunk.token_count,
            distance.label("distance"),
        )
        .join(Source, Source.id == SourceChunk.source_id)
        .where(
            SourceChunk.workspace_id == workspace_id,
            Source.workspace_id == workspace_id,
            Source.status == SourceStatus.READY,
            SourceChunk.embedding_model == provider.model,
        )
        .order_by(distance)
        .limit(top_k * _CANDIDATES_PER_RESULT)
        .subquery()
    )
    # The floor goes outside the LIMIT: an index scan cannot stop at a
    # distance, only at a count.
    statement = (
        select(nearest)
        .where(nearest.c.distance <= 1 - floor)
        .order_by(nearest.c.distance)
    )
    await db.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
    await db.execute(
        text("SELECT set_config('hnsw.ef_search', :value, true)"),
        {"value": str(max(40, top_k * _CANDIDATES_PER_RESULT))},
    )
    rows = (await db.execute(statement)).all()

    chosen: list[RetrievedChunk] = []
    seen: dict[uuid.UUID, list[set[str]]] = {}
    budget = settings.retrieval_token_budget
    for row in rows:
        words = _words(row.content)
        if any(_near_identical(words, other) for other in seen.get(row.source_id, [])):
            continue
        if row.token_count > budget:
            continue
        seen.setdefault(row.source_id, []).append(words)
        budget -= row.token_count
        chosen.append(
            RetrievedChunk(
                chunk_id=row.id,
                source_id=row.source_id,
                filename=row.filename,
                content=row.content,
                page_number=row.page_number,
                char_start=row.char_start,
                char_end=row.char_end,
                token_count=row.token_count,
                similarity=1 - float(row.distance),
            )
        )
        if len(chosen) == top_k:
            break
    return Retrieval(chosen, floor, searched=True)
