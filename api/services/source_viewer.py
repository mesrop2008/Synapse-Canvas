"""What the source viewer reads: a source's extracted text, its passages
with the offsets citations use, and for a Word file a rendering of it.

The file itself goes out through the API with the workspace check, never
by a storage path or a static URL, so membership guards it as it guards
the rest of the workspace."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.exceptions import AppError, ErrorCode
from api.models.enums import SourceStatus
from api.models.source import Source
from api.models.source_chunk import SourceChunk
from api.models.source_text import SourceText
from api.services import docx_import, ingestion, source_files
from api.services.extraction import ExtractionError
from api.services.prosemirror_html import to_html
from api.services.sources import SourceNotFoundError, get_source
from api.storage import SourceStorage

logger = logging.getLogger(__name__)

SourceFormat = Literal["pdf", "docx", "markdown", "text"]

_FORMATS: dict[str, SourceFormat] = {
    source_files.PDF: "pdf",
    source_files.DOCX: "docx",
    source_files.MARKDOWN: "markdown",
    source_files.PLAIN_TEXT: "text",
}
_CHARSET_SNIFF = 64 * 1024


class SourceNotViewableError(AppError):
    status_code = 415
    detail = "The viewer cannot show this kind of file; it can be downloaded"
    code = ErrorCode.SOURCE_NOT_VIEWABLE


class SourceTextUnavailableError(AppError):
    status_code = 409
    detail = "The source has not been read, or could not be"
    code = ErrorCode.SOURCE_TEXT_UNAVAILABLE


@dataclass(frozen=True, slots=True)
class Passage:
    id: uuid.UUID
    chunk_index: int
    char_start: int
    char_end: int
    page_number: int | None
    token_count: int


@dataclass(frozen=True, slots=True)
class SourceView:
    source: Source
    format: SourceFormat
    text: str
    page_starts: list[int]
    passages: list[Passage]
    # A Word file as HTML; None for other formats, or when the file cannot
    # be rendered, and the text is shown instead.
    html: str | None


@dataclass(frozen=True, slots=True)
class SourceContent:
    source: Source
    media_type: str
    blocks: AsyncIterator[bytes]


async def open_content(
    db: AsyncSession,
    storage: SourceStorage,
    workspace_id: uuid.UUID,
    source_id: uuid.UUID,
) -> SourceContent:
    """The original file, as uploaded. Text is declared in the encoding it
    was read in, so the browser shows what the AI read."""
    source = await get_source(db, workspace_id, source_id)
    try:
        blocks = await storage.stream(source.storage_path)
    except FileNotFoundError as exc:
        logger.error("The file of source %s is missing from storage", source.id)
        raise SourceNotFoundError() from exc

    media_type = source.content_type
    if media_type.startswith("text/"):
        head = await anext(blocks, b"")
        if charset := source_files.text_charset(head[:_CHARSET_SNIFF]):
            media_type = f"{media_type}; charset={charset}"
        blocks = _prepend(head, blocks)
    return SourceContent(source=source, media_type=media_type, blocks=blocks)


async def _prepend(head: bytes, rest: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    if head:
        yield head
    async for block in rest:
        yield block


async def view(
    db: AsyncSession,
    storage: SourceStorage,
    workspace_id: uuid.UUID,
    source_id: uuid.UUID,
) -> SourceView:
    source = await get_source(db, workspace_id, source_id)
    kind = _FORMATS.get(source.content_type)
    if kind is None:
        raise SourceNotViewableError()

    stored = await db.get(SourceText, source.id)
    data: bytes | None = None
    if stored is None:
        data, stored = await _read_again(db, storage, source)

    passages = [
        Passage(*row)
        for row in (
            await db.execute(
                select(
                    SourceChunk.id,
                    SourceChunk.chunk_index,
                    SourceChunk.char_start,
                    SourceChunk.char_end,
                    SourceChunk.page_number,
                    SourceChunk.token_count,
                )
                .where(SourceChunk.source_id == source.id)
                .order_by(SourceChunk.chunk_index)
            )
        ).all()
    ]

    html = None
    if kind == "docx":
        if data is None:
            data = await _read_file(storage, source)
        html = await asyncio.to_thread(_docx_html, data)
    return SourceView(
        source=source,
        format=kind,
        text=stored.text,
        page_starts=list(stored.page_starts),
        passages=passages,
        html=html,
    )


async def _read_file(storage: SourceStorage, source: Source) -> bytes:
    try:
        return await storage.read(source.storage_path)
    except FileNotFoundError as exc:
        logger.error("The file of source %s is missing from storage", source.id)
        raise SourceNotFoundError() from exc


async def _read_again(
    db: AsyncSession, storage: SourceStorage, source: Source
) -> tuple[bytes, SourceText]:
    """For a source read before its text was kept: extracted once more, as
    the worker does, and kept. Only a ready source is worth the work; one
    still queued will be stored by the worker, and a failed one would fail
    again."""
    if source.status is not SourceStatus.READY:
        raise SourceTextUnavailableError()
    data = await _read_file(storage, source)
    try:
        extracted = await ingestion.extract_file(data, source.content_type)
    except ExtractionError as exc:
        logger.warning("Source %s could not be read again: %s", source.id, exc.detail)
        raise SourceTextUnavailableError() from exc
    try:
        await ingestion.store_text(source.id, extracted)
    except IntegrityError as exc:
        raise SourceNotFoundError() from exc
    stored = await db.get(SourceText, source.id, populate_existing=True)
    assert stored is not None
    return data, stored


def _docx_html(data: bytes) -> str | None:
    try:
        imported = docx_import.import_docx(
            data, max_uncompressed_bytes=get_settings().source_max_uncompressed_bytes
        )
    except AppError:
        return None
    return to_html(imported.content)
