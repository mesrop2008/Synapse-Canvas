from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from api.services.retrieval import RetrievedChunk


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    top_k: int | None = Field(default=None, ge=1, le=20)


class SearchHit(BaseModel):
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    filename: str
    content: str
    page_number: int | None = Field(description="The page the passage starts on.")
    char_start: int = Field(description="Offset into the text extracted from the source.")
    char_end: int
    similarity: float = Field(description="Cosine similarity to the query, -1 to 1.")

    @classmethod
    def of(cls, chunk: RetrievedChunk) -> SearchHit:
        return cls(
            chunk_id=chunk.chunk_id,
            source_id=chunk.source_id,
            filename=chunk.filename,
            content=chunk.content,
            page_number=chunk.page_number,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            similarity=chunk.similarity,
        )


class SearchResults(BaseModel):
    results: list[SearchHit]
    min_similarity: float = Field(description="Passages scoring below this are left out.")
    searched: bool = Field(
        description="False when the workspace has no ready sources, so nothing was searched."
    )
