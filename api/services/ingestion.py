"""The worker's job: read a source, split it into passages, embed them in
batches and store them, then mark it ready -- or failed, with a code.

Safe to run twice, or after a crash part-way. A run first deletes the
source's existing chunks, except any it would produce again exactly, with
an embedding from the same model: those were paid for, so they are kept
rather than embedded again. Each batch is committed as it is embedded, so
a run that dies loses at most the batch in flight, and the unique
(source_id, chunk_index) constraint makes a duplicate impossible whatever
happens. Passages of a source that is not ready are never retrieved."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Iterator, Sequence
from datetime import timedelta

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from api.core.config import get_settings
from api.core.exceptions import ErrorCode
from api.db.session import get_engine, get_sessionmaker
from api.embeddings.base import EmbeddingProvider
from api.embeddings.errors import EmbeddingError
from api.models.enums import SourceStatus
from api.models.source import Source
from api.models.source_chunk import SourceChunk
from api.models.source_text import SourceText
from api.services import embedding_usage
from api.services.chunking import ChunkDraft, chunk_text
from api.services.embedding_usage import EmbeddingLimitError
from api.services.extraction import Extracted, ExtractionError, extract, extract_isolated
from api.storage import SourceStorage

logger = logging.getLogger(__name__)


def _lock_key(source_id: uuid.UUID) -> int:
    return int.from_bytes(source_id.bytes[8:], "big", signed=True)


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _batches(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def process_source(
    source_id: uuid.UUID, *, provider: EmbeddingProvider, storage: SourceStorage
) -> str:
    """Returns what happened, for the worker's log. Another run already
    working on the same source makes this one a no-op."""
    async with get_engine().connect() as connection:
        lock = await connection.execution_options(isolation_level="AUTOCOMMIT")
        key = _lock_key(source_id)
        if not await lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}):
            return "busy"
        try:
            return await _process(source_id, provider, storage)
        finally:
            await lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


async def _process(
    source_id: uuid.UUID, provider: EmbeddingProvider, storage: SourceStorage
) -> str:
    settings = get_settings()
    async with get_sessionmaker()() as db:
        source = await db.get(Source, source_id)
        if source is None:
            return "gone"
        if source.status in (SourceStatus.READY, SourceStatus.FAILED):
            return source.status.value
        source.status = SourceStatus.PROCESSING
        source.error_code = None
        await db.commit()
        workspace_id = source.workspace_id
        storage_path = source.storage_path
        content_type = source.content_type

    try:
        data = await storage.read(storage_path)
        extracted = await extract_file(data, content_type)
        await store_text(source_id, extracted)
        drafts = await asyncio.to_thread(
            chunk_text,
            extracted.text,
            extracted.page_starts,
            count_tokens=provider.count_tokens,
            target_tokens=settings.chunk_target_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
            max_tokens=min(settings.chunk_max_tokens, provider.max_input_tokens),
        )
        missing = await _keep_reusable(source_id, drafts, provider)

        # Identical passages -- a repeated header, say -- are embedded once.
        by_content: dict[str, list[ChunkDraft]] = {}
        for draft in missing:
            by_content.setdefault(draft.content, []).append(draft)
        for batch in _batches(list(by_content), settings.embedding_batch_size):
            await _embed_batch(
                source_id,
                workspace_id,
                [d for c in batch for d in by_content[c]],
                provider,
            )
    except ExtractionError as exc:
        logger.info("Source %s could not be read: %s", source_id, exc.detail)
        return await _finish(source_id, SourceStatus.FAILED, error_code=exc.code)
    except (EmbeddingError, EmbeddingLimitError) as exc:
        logger.warning("Source %s was not embedded: %s", source_id, exc.code.value)
        return await _finish(source_id, SourceStatus.FAILED, error_code=exc.code)
    except IntegrityError:
        # Deleted while it was being embedded.
        return "gone"
    except FileNotFoundError:
        logger.error("The file of source %s is missing from storage", source_id)
        return await _finish(
            source_id, SourceStatus.FAILED, error_code=ErrorCode.SERVER_ERROR
        )
    except Exception:
        logger.exception("Processing source %s failed", source_id)
        return await _finish(
            source_id, SourceStatus.FAILED, error_code=ErrorCode.SERVER_ERROR
        )

    return await _finish(
        source_id,
        SourceStatus.READY,
        chunk_count=len(drafts),
        page_count=extracted.page_count,
    )


async def extract_file(data: bytes, content_type: str) -> Extracted:
    settings = get_settings()
    if not settings.source_extract_isolated:
        return await asyncio.to_thread(extract, data, content_type)
    return await asyncio.to_thread(
        extract_isolated,
        data,
        content_type,
        timeout=settings.source_extract_timeout_seconds,
        memory_bytes=settings.source_extract_memory_bytes,
    )


async def store_text(source_id: uuid.UUID, extracted: Extracted) -> None:
    """Replaces what an earlier run stored. A source deleted meanwhile raises
    IntegrityError, as a passage stored for it would."""
    statement = insert(SourceText).values(
        source_id=source_id, text=extracted.text, page_starts=extracted.page_starts
    )
    statement = statement.on_conflict_do_update(
        index_elements=[SourceText.source_id],
        set_={
            "text": statement.excluded.text,
            "page_starts": statement.excluded.page_starts,
        },
    )
    async with get_sessionmaker()() as db:
        await db.execute(statement)
        await db.commit()


async def _keep_reusable(
    source_id: uuid.UUID, drafts: list[ChunkDraft], provider: EmbeddingProvider
) -> list[ChunkDraft]:
    """Deletes the source's chunks except those identical to a draft, and
    returns the drafts still to embed."""
    wanted = {
        draft.index: (
            _digest(draft.content),
            draft.char_start,
            draft.char_end,
            draft.page_number,
            provider.model,
            provider.dimension,
        )
        for draft in drafts
    }
    async with get_sessionmaker()() as db:
        rows = await db.execute(
            select(
                SourceChunk.id,
                SourceChunk.chunk_index,
                func.encode(
                    func.sha256(func.convert_to(SourceChunk.content, "UTF8")), "hex"
                ),
                SourceChunk.char_start,
                SourceChunk.char_end,
                SourceChunk.page_number,
                SourceChunk.embedding_model,
                SourceChunk.embedding_dimension,
            ).where(SourceChunk.source_id == source_id)
        )
        kept: dict[int, uuid.UUID] = {}
        for chunk_id, index, *identity in rows.all():
            if wanted.get(index) == tuple(identity):
                kept[index] = chunk_id
        await db.execute(
            delete(SourceChunk).where(
                SourceChunk.source_id == source_id, SourceChunk.id.not_in(kept.values())
            )
        )
        await db.commit()
    if kept:
        logger.info("Source %s: reusing %d embedded passages", source_id, len(kept))
    return [draft for draft in drafts if draft.index not in kept]


async def _embed_batch(
    source_id: uuid.UUID,
    workspace_id: uuid.UUID,
    drafts: list[ChunkDraft],
    provider: EmbeddingProvider,
) -> None:
    texts = list(dict.fromkeys(draft.content for draft in drafts))
    async with get_sessionmaker()() as db:
        batch = await embedding_usage.embed(
            db,
            provider,
            texts,
            kind="document",
            workspace_id=workspace_id,
            source_id=source_id,
        )
        vectors = dict(zip(texts, batch.vectors, strict=True))
        db.add_all(
            SourceChunk(
                source_id=source_id,
                workspace_id=workspace_id,
                chunk_index=draft.index,
                content=draft.content,
                embedding=vectors[draft.content],
                embedding_model=provider.model,
                embedding_dimension=provider.dimension,
                page_number=draft.page_number,
                char_start=draft.char_start,
                char_end=draft.char_end,
                token_count=draft.token_count,
            )
            for draft in drafts
        )
        # Progress, for the sweep that looks for stalled sources.
        await db.execute(
            update(Source)
            .where(Source.id == source_id)
            .values(updated_at=func.clock_timestamp())
        )
        await db.commit()


async def _finish(
    source_id: uuid.UUID,
    status: SourceStatus,
    *,
    error_code: ErrorCode | None = None,
    chunk_count: int | None = None,
    page_count: int | None = None,
) -> str:
    values: dict[str, object] = {
        "status": status,
        "error_code": error_code.value if error_code else None,
        "processed_at": func.now(),
    }
    if chunk_count is not None:
        values["chunk_count"] = chunk_count
    if page_count is not None:
        values["page_count"] = page_count
    async with get_sessionmaker()() as db:
        await db.execute(
            update(Source)
            .where(Source.id == source_id, Source.status == SourceStatus.PROCESSING)
            .values(**values)
        )
        await db.commit()
    return status.value if error_code is None else f"{status.value}: {error_code.value}"


async def stalled_sources(
    *, pending_for: timedelta, processing_for: timedelta
) -> list[uuid.UUID]:
    """Pending ones whose job was lost -- Redis restarted, or was down at
    upload -- and processing ones whose worker died without Arq noticing."""
    async with get_sessionmaker()() as db:
        rows = await db.scalars(
            select(Source.id).where(
                or_(
                    and_(
                        Source.status == SourceStatus.PENDING,
                        Source.updated_at < func.now() - pending_for,
                    ),
                    and_(
                        Source.status == SourceStatus.PROCESSING,
                        Source.updated_at < func.now() - processing_for,
                    ),
                )
            )
        )
        return list(rows.all())
