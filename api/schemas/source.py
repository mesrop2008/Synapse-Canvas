from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from api.models.enums import SourceStatus
from api.models.source import Source
from api.models.source_chunk import SourceChunk
from api.services.sources import is_retryable


class SourceRead(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    uploaded_by: uuid.UUID | None
    filename: str
    content_type: str = Field(description="Sniffed from the file's bytes.")
    size_bytes: int
    checksum: str = Field(description="SHA-256 of the file, hex.")
    status: SourceStatus
    error_code: str | None = Field(
        description="Why it failed; the client translates it."
    )
    retryable: bool = Field(
        description="Failed for a reason another try may fix (a quota, an outage), "
        "not one the same file will always hit (unreadable, no text)."
    )
    page_count: int | None
    chunk_count: int
    created_at: datetime
    processed_at: datetime | None

    @classmethod
    def of(cls, source: Source) -> SourceRead:
        return cls(
            id=source.id,
            workspace_id=source.workspace_id,
            uploaded_by=source.uploaded_by,
            filename=source.filename,
            content_type=source.content_type,
            size_bytes=source.size_bytes,
            checksum=source.checksum,
            status=source.status,
            error_code=source.error_code,
            retryable=is_retryable(source),
            page_count=source.page_count,
            chunk_count=source.chunk_count,
            created_at=source.created_at,
            processed_at=source.processed_at,
        )


class SourceChunkRead(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    filename: str
    chunk_index: int
    content: str
    page_number: int | None = Field(description="The page the passage starts on.")
    char_start: int = Field(description="Offset into the text extracted from the source.")
    char_end: int

    @classmethod
    def of(cls, source: Source, chunk: SourceChunk) -> SourceChunkRead:
        return cls(
            id=chunk.id,
            source_id=source.id,
            filename=source.filename,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            page_number=chunk.page_number,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
        )
