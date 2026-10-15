"""Uploaded sources: accepting a file, listing, deleting and retrying. The
work of reading and embedding one is the worker's (`services.ingestion`)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import BinaryIO

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.exceptions import AppError, ConflictError, ErrorCode, NotFoundError
from api.core.queue import JobQueue
from api.models.enums import SourceStatus
from api.models.source import Source
from api.models.source_chunk import SourceChunk
from api.services.source_files import measure, sanitize_filename, sniff
from api.storage import SourceStorage

logger = logging.getLogger(__name__)

# Failures that the same bytes will always reproduce. Anything else -- a
# quota, an outage, a crash -- is worth another try.
PERMANENT_FAILURES = frozenset(
    {
        ErrorCode.SOURCE_PARSE_FAILED,
        ErrorCode.SOURCE_NO_TEXT,
        ErrorCode.SOURCE_ENCRYPTED,
        ErrorCode.SOURCE_EMPTY,
        ErrorCode.EMBEDDING_INPUT_TOO_LONG,
    }
)

# Distinct from the AI usage lock, which is keyed on the workspace id alone.
_STORAGE_LOCK = 0x5C


class SourceNotFoundError(NotFoundError):
    detail = "Source not found"
    code = ErrorCode.SOURCE_NOT_FOUND


class ChunkNotFoundError(NotFoundError):
    detail = "That passage no longer exists"
    code = ErrorCode.SOURCE_CHUNK_NOT_FOUND


class SourceNotFailedError(ConflictError):
    detail = "Only a source that failed can be retried"
    code = ErrorCode.SOURCE_NOT_FAILED


class StorageQuotaError(AppError):
    status_code = 413
    code = ErrorCode.SOURCE_STORAGE_QUOTA

    def __init__(self, used: int, size: int, quota: int) -> None:
        super().__init__(
            f"This workspace stores {used} of its {quota} bytes; a {size}-byte "
            "file does not fit. Delete a source first."
        )


@dataclass(frozen=True, slots=True)
class Upload:
    source: Source
    created: bool


def storage_key(workspace_id: uuid.UUID, source_id: uuid.UUID) -> str:
    return f"{workspace_id}/{source_id}"


def is_retryable(source: Source) -> bool:
    return source.status is SourceStatus.FAILED and (
        source.error_code not in PERMANENT_FAILURES
    )


async def storage_used(db: AsyncSession, workspace_id: uuid.UUID) -> int:
    used = await db.scalar(
        select(func.coalesce(func.sum(Source.size_bytes), 0)).where(
            Source.workspace_id == workspace_id
        )
    )
    return int(used or 0)


async def _find_duplicate(
    db: AsyncSession, workspace_id: uuid.UUID, checksum: str
) -> Source | None:
    return await db.scalar(
        select(Source).where(
            Source.workspace_id == workspace_id, Source.checksum == checksum
        )
    )


async def upload(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: BinaryIO,
    filename: str | None,
    storage: SourceStorage,
    queue: JobQueue,
) -> Upload:
    """Validates, stores and records the file, then queues it. An identical
    file already in the workspace is returned as it is: embedding it twice
    would spend the daily quota for nothing."""
    settings = get_settings()
    name = sanitize_filename(filename)
    measured = await asyncio.to_thread(measure, data, settings.source_max_file_bytes)
    sniffed = await asyncio.to_thread(
        sniff, data, name, max_uncompressed_bytes=settings.source_max_uncompressed_bytes
    )

    existing = await _find_duplicate(db, workspace_id, measured.checksum)
    if existing is not None:
        return Upload(existing, created=False)

    # Held to the commit, so two uploads cannot both take the last space.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:space, hashtext(:workspace))"),
        {"space": _STORAGE_LOCK, "workspace": str(workspace_id)},
    )
    used = await storage_used(db, workspace_id)
    quota = settings.source_workspace_quota_bytes
    if quota > 0 and used + measured.size > quota:
        raise StorageQuotaError(used, measured.size, quota)

    source_id = uuid.uuid4()
    key = storage_key(workspace_id, source_id)
    source = Source(
        id=source_id,
        workspace_id=workspace_id,
        uploaded_by=user_id,
        filename=name,
        content_type=sniffed.content_type,
        size_bytes=measured.size,
        storage_path=key,
        checksum=measured.checksum,
        status=SourceStatus.PENDING,
    )
    await storage.save(key, data)
    db.add(source)
    try:
        await db.commit()
    except IntegrityError:
        # The same file, uploaded by someone else a moment ago.
        await db.rollback()
        await storage.delete(key)
        existing = await _find_duplicate(db, workspace_id, measured.checksum)
        if existing is None:
            raise
        return Upload(existing, created=False)
    except BaseException:
        await storage.delete(key)
        raise

    await _enqueue(queue, source.id)
    return Upload(source, created=True)


async def _enqueue(queue: JobQueue, source_id: uuid.UUID) -> None:
    """A failure here is logged, not raised: the row is committed, and the
    worker's sweep picks up sources left pending."""
    try:
        await queue.enqueue_source(source_id)
    except Exception:
        logger.exception("Could not queue source %s; the sweep will", source_id)


async def list_sources(db: AsyncSession, workspace_id: uuid.UUID) -> list[Source]:
    rows = await db.scalars(
        select(Source)
        .where(Source.workspace_id == workspace_id)
        .order_by(Source.created_at.desc(), Source.id.desc())
    )
    return list(rows.all())


async def get_source(
    db: AsyncSession, workspace_id: uuid.UUID, source_id: uuid.UUID
) -> Source:
    source = await db.scalar(
        select(Source)
        .where(Source.id == source_id, Source.workspace_id == workspace_id)
        .execution_options(populate_existing=True)
    )
    if source is None:
        raise SourceNotFoundError()
    return source


async def get_chunk(
    db: AsyncSession, workspace_id: uuid.UUID, source_id: uuid.UUID, chunk_id: uuid.UUID
) -> tuple[Source, SourceChunk]:
    """What a citation opens. Source first, so a deleted one says so."""
    source = await get_source(db, workspace_id, source_id)
    chunk = await db.scalar(
        select(SourceChunk).where(
            SourceChunk.id == chunk_id,
            SourceChunk.source_id == source.id,
            SourceChunk.workspace_id == workspace_id,
        )
    )
    if chunk is None:
        raise ChunkNotFoundError()
    return source, chunk


async def delete_source(db: AsyncSession, source: Source, storage: SourceStorage) -> None:
    """The row first: its chunks go with it, and a file left behind by a
    failed removal is only wasted space, while a row without its file
    would be a source that can never be retried."""
    key = source.storage_path
    await db.delete(source)
    await db.commit()
    try:
        await storage.delete(key)
    except OSError:
        logger.exception("Could not remove the file of a deleted source: %s", key)


async def retry_source(db: AsyncSession, source: Source, queue: JobQueue) -> Source:
    if source.status is not SourceStatus.FAILED:
        raise SourceNotFailedError()
    source.status = SourceStatus.PENDING
    source.error_code = None
    await db.commit()
    await _enqueue(queue, source.id)
    return source


async def delete_workspace_files(storage: SourceStorage, workspace_id: uuid.UUID) -> None:
    try:
        await storage.delete_prefix(str(workspace_id))
    except OSError:
        logger.exception("Could not remove the files of workspace %s", workspace_id)
