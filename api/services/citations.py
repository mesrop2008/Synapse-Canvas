"""Markers in a model's answer, resolved on the server against the passages
the request actually sent.

The model only ever sees and writes numbers. The source, page and offsets
of a citation come from the passage behind its number, so a marker the
model invents -- [7] when three passages were sent -- resolves to nothing
and is dropped, and no filename or link it writes is ever trusted."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.source_chunk import SourceChunk
from api.services.retrieval import RetrievedChunk

# [1], and the grouped forms models also write: [1, 2] and [1; 3].
_MARKERS = re.compile(r"\[(\d{1,3}(?:\s*[,;]\s*\d{1,3})*)\]")


@dataclass(frozen=True, slots=True)
class Citation:
    marker: int
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    filename: str
    page_number: int | None
    char_start: int
    char_end: int

    def as_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["chunk_id"] = str(self.chunk_id)
        data["source_id"] = str(self.source_id)
        return data


def cited_markers(response: str) -> list[int]:
    """Every number cited, once each, in the order first cited."""
    found: dict[int, None] = {}
    for group in _MARKERS.findall(response):
        for number in re.split(r"\s*[,;]\s*", group):
            found.setdefault(int(number), None)
    return list(found)


def resolve(response: str, sent: Sequence[RetrievedChunk]) -> list[Citation]:
    """Marker n is the n-th passage sent; anything else is discarded."""
    citations = []
    for marker in cited_markers(response):
        if not 1 <= marker <= len(sent):
            continue
        chunk = sent[marker - 1]
        citations.append(
            Citation(
                marker=marker,
                chunk_id=chunk.chunk_id,
                source_id=chunk.source_id,
                filename=chunk.filename,
                page_number=chunk.page_number,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
            )
        )
    return citations


async def existing_chunks(
    db: AsyncSession, chunk_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    """Of these passages, the ones still there. A passage goes with its
    source, so a missing one means the source was deleted."""
    wanted = set(chunk_ids)
    if not wanted:
        return set()
    rows = await db.scalars(select(SourceChunk.id).where(SourceChunk.id.in_(wanted)))
    return set(rows.all())
