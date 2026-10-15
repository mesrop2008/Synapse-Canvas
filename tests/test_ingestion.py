"""The worker's side: from an uploaded file to embedded passages, and every
way that can fail.

The job opens sessions of its own, which cannot see a test's transaction,
so these tests commit real rows through the session factory and delete
what they made afterwards, as test_ai.py does."""

from __future__ import annotations

import io
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from arq.connections import ArqRedis
from arq.worker import Worker
from httpx import ASGITransport, AsyncClient
from pypdf import PdfReader, PdfWriter
from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update

from api.core.config import get_settings
from api.core.queue import ArqQueue
from api.core.security import create_access_token
from api.db.session import get_engine, get_sessionmaker
from api.embeddings.errors import (
    EmbeddingFailedError,
    RateLimitedError,
    UpstreamUnavailableError,
)
from api.embeddings.fake import FakeEmbeddingProvider
from api.main import create_app
from api.models import (
    EmbeddingRequest,
    Source,
    SourceChunk,
    User,
    Workspace,
    WorkspaceMember,
)
from api.models.enums import SourceStatus, WorkspaceRole
from api.services import embedding_usage, ingestion
from api.services import sources as source_service
from api.services.chunking import ChunkDraft
from api.services.extraction import ExtractionError, extract, extract_isolated
from api.storage import LocalFileStorage
from api.worker import WorkerSettings, requeue_stalled
from tests.sources_helpers import RecordingQueue, docx_bytes, pdf_bytes, upload_body

PARAGRAPH = (
    "Wetlands store carbon in waterlogged soil, where decomposition is slow. "
    "Draining them releases that carbon as the peat oxidises. "
)


def long_text(paragraphs: int = 12) -> str:
    """Every sentence different, so no two passages are identical."""
    return "\n\n".join(
        " ".join(f"Section {i}, note {j}: {PARAGRAPH.strip()}" for j in range(4))
        for i in range(paragraphs)
    )


@dataclass
class World:
    user_id: uuid.UUID
    workspace_id: uuid.UUID
    token: str
    storage: LocalFileStorage

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.token}


@pytest_asyncio.fixture
async def world(redis_client: Redis, tmp_path: Path) -> AsyncIterator[World]:
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    marker = uuid.uuid4().hex[:12]
    async with get_sessionmaker()() as db:
        user = User(
            email=f"ingest-{marker}@example.com", hashed_password="x", name="Ingest"
        )
        db.add(user)
        await db.flush()
        workspace = Workspace(name="Sources", owner_id=user.id)
        db.add(workspace)
        await db.flush()
        db.add(
            WorkspaceMember(
                workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.OWNER
            )
        )
        await db.commit()
        made = World(
            user.id,
            workspace.id,
            create_access_token(user.id),
            LocalFileStorage(tmp_path / "sources"),
        )
    try:
        yield made
    finally:
        async with get_sessionmaker()() as db:
            await db.execute(delete(User).where(User.id == made.user_id))
            await db.commit()
        await get_engine().dispose()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()


@pytest.fixture
def settings_override() -> Any:
    settings = get_settings()
    saved: dict[str, Any] = {}

    def _apply(**overrides: Any) -> None:
        for key, value in overrides.items():
            saved.setdefault(key, getattr(settings, key))
            setattr(settings, key, value)

    yield _apply
    for key, value in saved.items():
        setattr(settings, key, value)


@pytest.fixture
def small_chunks(settings_override: Any) -> None:
    """Several passages from a short file, and batches of two."""
    settings_override(
        chunk_target_tokens=60,
        chunk_overlap_tokens=10,
        chunk_max_tokens=80,
        embedding_batch_size=2,
        embedding_backoff_base_seconds=0.001,
        embedding_backoff_max_seconds=0.01,
    )


async def add_source(world: World, name: str, data: bytes) -> uuid.UUID:
    async with get_sessionmaker()() as db:
        uploaded = await source_service.upload(
            db,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            data=io.BytesIO(data),
            filename=name,
            storage=world.storage,
            queue=RecordingQueue(),
        )
        return uploaded.source.id


async def run(world: World, source_id: uuid.UUID, provider: FakeEmbeddingProvider) -> str:
    return await ingestion.process_source(source_id, provider=provider, storage=world.storage)


async def load(source_id: uuid.UUID) -> Source:
    async with get_sessionmaker()() as db:
        source = await db.get(Source, source_id)
        assert source is not None
        return source


async def chunks_of(source_id: uuid.UUID) -> list[SourceChunk]:
    async with get_sessionmaker()() as db:
        rows = await db.scalars(
            select(SourceChunk)
            .where(SourceChunk.source_id == source_id)
            .order_by(SourceChunk.chunk_index)
        )
        return list(rows.all())


async def requests_made(world: World) -> int:
    async with get_sessionmaker()() as db:
        return await embedding_usage.requests_today(db, world.workspace_id)


async def set_status(source_id: uuid.UUID, status: SourceStatus) -> None:
    async with get_sessionmaker()() as db:
        await db.execute(update(Source).where(Source.id == source_id).values(status=status))
        await db.commit()


def arq_pool() -> ArqRedis:
    url = os.environ.get("TEST_REDIS_URL")
    if url:
        return ArqRedis.from_url(url)
    import fakeredis

    from tests import conftest

    if conftest._fake_redis_server is None:
        conftest._fake_redis_server = fakeredis.FakeServer()
    fake = fakeredis.FakeAsyncRedis(server=conftest._fake_redis_server)
    return ArqRedis(connection_pool=fake.connection_pool)


async def run_worker(pool: ArqRedis, world: World, provider: FakeEmbeddingProvider) -> None:
    worker = Worker(
        functions=WorkerSettings.functions,
        redis_pool=pool,
        burst=True,
        poll_delay=0.05,
        handle_signals=False,
        ctx={"embeddings": provider, "storage": world.storage},
    )
    # Not worker.close(): it raises SIGUSR1, which Windows lacks. Burst mode
    # returns once the queue is empty, and the caller closes the pool.
    await worker.main()


# --- Through the worker ---


async def test_an_upload_becomes_ready_through_the_worker(world: World) -> None:
    pool = arq_pool()
    app = create_app()
    app.state.storage = world.storage
    app.state.jobs = ArqQueue(get_settings().redis_url, pool=pool)
    provider = FakeEmbeddingProvider()
    pages = ["Page one is about peat.\n" + PARAGRAPH, "Page two is about rivers.\n" + PARAGRAPH]
    data = pdf_bytes(*pages)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        base = f"/workspaces/{world.workspace_id}/sources"
        uploaded = await http.post(base, headers=world.headers, **upload_body("bog.pdf", data))
        assert uploaded.status_code == 201, uploaded.text
        source_id = uploaded.json()["id"]

        await run_worker(pool, world, provider)
        fetched = await http.get(f"{base}/{source_id}", headers=world.headers)

    await pool.aclose()
    body = fetched.json()
    assert body["status"] == "ready", body
    assert body["page_count"] == 2
    assert body["chunk_count"] >= 1
    assert body["processed_at"] is not None

    text = extract(data, "application/pdf").text
    stored = await chunks_of(uuid.UUID(source_id))
    assert len(stored) == body["chunk_count"]
    for chunk in stored:
        assert chunk.content == text[chunk.char_start : chunk.char_end]
        assert chunk.embedding_model == "fake-embedding-1"
        assert chunk.embedding_dimension == 1536
        assert len(chunk.embedding) == 1536
        assert chunk.workspace_id == world.workspace_id
    assert stored[0].page_number == 1
    assert provider.calls, "embedded in a batch request"


async def test_passages_record_their_page_and_offsets(world: World, small_chunks: None) -> None:
    pages = [f"Page {n}.\n" + PARAGRAPH * 3 for n in range(1, 4)]
    data = pdf_bytes(*pages)
    source_id = await add_source(world, "three.pdf", data)

    assert await run(world, source_id, FakeEmbeddingProvider()) == "ready"

    extracted = extract(data, "application/pdf")
    stored = await chunks_of(source_id)
    assert {chunk.page_number for chunk in stored} == {1, 2, 3}
    for chunk in stored:
        assert chunk.content == extracted.text[chunk.char_start : chunk.char_end]
        page_start = extracted.page_starts[chunk.page_number - 1]
        assert page_start <= chunk.char_start
        assert chunk.token_count <= 80
    assert (await load(source_id)).page_count == 3


async def test_a_word_document_is_read_with_its_tables(world: World) -> None:
    data = docx_bytes(
        "Field notes.", "Water table readings follow.", table=[["Site", "Depth"], ["A", "40 cm"]]
    )
    source_id = await add_source(world, "notes.docx", data)

    assert await run(world, source_id, FakeEmbeddingProvider()) == "ready"

    [chunk] = await chunks_of(source_id)
    assert "Site | Depth" in chunk.content
    assert "A | 40 cm" in chunk.content
    assert (await load(source_id)).page_count is None


async def test_batches_never_send_one_request_per_passage(
    world: World, small_chunks: None
) -> None:
    provider = FakeEmbeddingProvider()
    source_id = await add_source(world, "long.txt", long_text().encode())

    await run(world, source_id, provider)

    stored = await chunks_of(source_id)
    assert len(stored) > 4
    assert all(len(call) <= 2 for call in provider.calls)
    assert len(provider.calls) == await requests_made(world) < len(stored)


# --- Idempotency ---


async def test_running_the_job_again_duplicates_nothing_and_embeds_nothing(
    world: World, small_chunks: None
) -> None:
    provider = FakeEmbeddingProvider()
    source_id = await add_source(world, "long.txt", long_text().encode())
    await run(world, source_id, provider)
    first = await chunks_of(source_id)
    calls = len(provider.calls)

    # As if Arq delivered it again after a crash before the status changed.
    await set_status(source_id, SourceStatus.PROCESSING)
    assert await run(world, source_id, provider) == "ready"

    second = await chunks_of(source_id)
    assert [c.id for c in second] == [c.id for c in first]
    assert len(provider.calls) == calls
    assert (await load(source_id)).chunk_count == len(second)


async def test_a_ready_source_is_left_alone(world: World) -> None:
    provider = FakeEmbeddingProvider()
    source_id = await add_source(world, "a.txt", b"One short passage.")
    await run(world, source_id, provider)

    assert await run(world, source_id, provider) == "ready"
    assert len(provider.calls) == 1


async def test_a_retry_after_a_failure_embeds_only_what_is_missing(
    world: World, small_chunks: None
) -> None:
    provider = FakeEmbeddingProvider()
    source_id = await add_source(world, "long.txt", long_text().encode())
    await run(world, source_id, provider)
    expected = [(c.chunk_index, c.content) for c in await chunks_of(source_id)]
    batches = len(provider.calls)
    assert batches >= 3

    # Again from scratch, failing on the second batch.
    async with get_sessionmaker()() as db:
        await db.execute(delete(SourceChunk).where(SourceChunk.source_id == source_id))
        await db.execute(delete(EmbeddingRequest))
        await db.commit()
    await set_status(source_id, SourceStatus.PENDING)
    failing = FakeEmbeddingProvider()
    original = failing._embed

    async def fail_second(texts: list[str], kind: Any) -> Any:
        if len(failing.calls) == 1:
            failing.calls.append(texts)
            raise EmbeddingFailedError()
        return await original(texts, kind)

    failing._embed = fail_second  # type: ignore[method-assign]

    assert await run(world, source_id, failing) == "failed: embedding.failed"
    source = await load(source_id)
    assert source.status is SourceStatus.FAILED
    assert source.error_code == "embedding.failed"
    assert len(await chunks_of(source_id)) == 2  # the first batch was kept

    async with get_sessionmaker()() as db:
        retried = await source_service.retry_source(
            db, await db.get(Source, source_id), RecordingQueue()  # type: ignore[arg-type]
        )
        assert retried.status is SourceStatus.PENDING
    resumed = FakeEmbeddingProvider()
    assert await run(world, source_id, resumed) == "ready"

    assert [(c.chunk_index, c.content) for c in await chunks_of(source_id)] == expected
    assert sum(len(call) for call in resumed.calls) == len(expected) - 2
    assert await requests_made(world) == 2 + len(resumed.calls)


async def test_a_source_deleted_mid_run_is_dropped_quietly(world: World) -> None:
    source_id = await add_source(world, "a.txt", b"Gone before it is embedded.")
    provider = FakeEmbeddingProvider()
    original = provider._embed

    async def delete_first(texts: list[str], kind: Any) -> Any:
        async with get_sessionmaker()() as db:
            await db.execute(delete(Source).where(Source.id == source_id))
            await db.commit()
        return await original(texts, kind)

    provider._embed = delete_first  # type: ignore[method-assign]

    assert await run(world, source_id, provider) == "gone"


# --- Input limit and quota ---


async def test_a_passage_over_the_input_limit_never_reaches_the_provider(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeEmbeddingProvider(max_input_tokens=100)
    huge = "word " * 400
    monkeypatch.setattr(
        ingestion,
        "chunk_text",
        lambda *args, **kwargs: [ChunkDraft(0, huge, 0, len(huge), None, 400)],
    )
    source_id = await add_source(world, "a.txt", huge.encode())

    assert await run(world, source_id, provider) == "failed: embedding.input_too_long"

    assert provider.calls == []
    assert await requests_made(world) == 0
    assert (await load(source_id)).error_code == "embedding.input_too_long"


async def test_a_429_is_retried_with_backoff_then_fails_as_quota(
    world: World, small_chunks: None, settings_override: Any
) -> None:
    settings_override(embedding_max_retries=3)
    provider = FakeEmbeddingProvider(failures=[RateLimitedError()] * 4)
    source_id = await add_source(world, "a.txt", b"Refused every time.")

    assert await run(world, source_id, provider) == "failed: embedding.quota_exhausted"

    assert len(provider.calls) == 4
    assert await requests_made(world) == 4, "every attempt counts"
    source = await load(source_id)
    assert source.error_code == "embedding.quota_exhausted"
    assert source_service.is_retryable(source)


async def test_a_daily_quota_is_not_retried(world: World, small_chunks: None) -> None:
    provider = FakeEmbeddingProvider(failures=[RateLimitedError(daily=True)] * 5)
    source_id = await add_source(world, "a.txt", b"Refused for the day.")

    assert await run(world, source_id, provider) == "failed: embedding.quota_exhausted"
    assert len(provider.calls) == 1


async def test_a_brief_outage_is_retried_through(world: World, small_chunks: None) -> None:
    provider = FakeEmbeddingProvider(failures=[UpstreamUnavailableError(), RateLimitedError()])
    source_id = await add_source(world, "a.txt", b"Third time lucky.")

    assert await run(world, source_id, provider) == "ready"
    assert len(provider.calls) == 3


async def test_backoff_doubles_and_waits_as_long_as_the_provider_asks(
    world: World, settings_override: Any
) -> None:
    settings_override(
        embedding_max_retries=4,
        embedding_backoff_base_seconds=1.0,
        embedding_backoff_max_seconds=8.0,
    )
    provider = FakeEmbeddingProvider(
        failures=[RateLimitedError()] * 3 + [RateLimitedError(retry_after=30)]
    )
    waited: list[float] = []

    async def sleep(seconds: float) -> None:
        waited.append(seconds)

    async with get_sessionmaker()() as db:
        await embedding_usage.embed(
            db, provider, ["text"], kind="document", workspace_id=world.workspace_id, sleep=sleep
        )

    assert [0.5 <= waited[0] <= 1, 1 <= waited[1] <= 2, 2 <= waited[2] <= 4] == [True] * 3
    # Asked for 30 seconds; capped at the configured maximum.
    assert waited[3] == 8.0


async def test_the_workspace_ceiling_stops_a_metered_provider(
    world: World, small_chunks: None, settings_override: Any
) -> None:
    settings_override(embedding_daily_request_limit=1)
    provider = FakeEmbeddingProvider(metered=True)
    source_id = await add_source(world, "long.txt", long_text(4).encode())

    assert await run(world, source_id, provider) == "failed: embedding.daily_limit"

    assert len(provider.calls) == 1
    assert await requests_made(world) == 1
    assert source_service.is_retryable(await load(source_id))


async def test_an_unmetered_provider_has_no_ceiling(
    world: World, small_chunks: None, settings_override: Any
) -> None:
    settings_override(embedding_daily_request_limit=1)
    source_id = await add_source(world, "long.txt", long_text(4).encode())

    assert await run(world, source_id, FakeEmbeddingProvider()) == "ready"
    assert await requests_made(world) > 1


# --- Files that cannot be read ---


def encrypted_pdf() -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes("Secret findings."))))
    writer.encrypt("hunter2", algorithm="RC4-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("scan.pdf", pdf_bytes(None, None), "source.no_text"),
        ("locked.pdf", encrypted_pdf(), "source.encrypted"),
        ("broken.pdf", b"%PDF-1.7\n" + b"\x00\xff garbage " * 40, "source.parse_failed"),
        ("blank.docx", docx_bytes(""), "source.empty"),
        ("spaces.txt", b"   \n\n\t  ", "source.empty"),
    ],
    ids=["scanned", "encrypted", "corrupt", "empty-docx", "blank-text"],
)
async def test_an_unreadable_file_fails_for_good_with_its_reason(
    world: World, name: str, data: bytes, code: str
) -> None:
    provider = FakeEmbeddingProvider()
    source_id = await add_source(world, name, data)

    assert await run(world, source_id, provider) == f"failed: {code}"

    source = await load(source_id)
    assert source.status is SourceStatus.FAILED
    assert source.error_code == code
    assert not source_service.is_retryable(source)
    assert await chunks_of(source_id) == []
    assert provider.calls == []


async def test_isolated_extraction_reads_a_file_in_a_child_process(
    world: World, settings_override: Any
) -> None:
    settings_override(source_extract_isolated=True)
    source_id = await add_source(world, "a.pdf", pdf_bytes("Parsed in a child process."))

    assert await run(world, source_id, FakeEmbeddingProvider()) == "ready"


def test_a_parser_that_overruns_is_killed() -> None:
    with pytest.raises(ExtractionError) as raised:
        extract_isolated(pdf_bytes("Too slow."), "application/pdf", timeout=0.001, memory_bytes=0)

    assert raised.value.code.value == "source.parse_failed"
    assert "longer than" in raised.value.detail


# --- The sweep ---


async def test_a_source_whose_job_was_lost_is_queued_again_once(world: World) -> None:
    source_id = await add_source(world, "a.txt", b"Its job vanished with Redis.")
    fresh_id = await add_source(world, "b.txt", b"Uploaded a moment ago.")
    async with get_sessionmaker()() as db:
        await db.execute(
            update(Source)
            .where(Source.id == source_id)
            .values(updated_at=func.now() - timedelta(minutes=5))
        )
        await db.commit()
    pool = arq_pool()
    await pool.flushdb()

    try:
        first = await requeue_stalled({"redis": pool})
        second = await requeue_stalled({"redis": pool})
        queued = await pool.queued_jobs()
    finally:
        await pool.aclose()

    assert (first, second) == (1, 0)
    assert [job.args for job in queued] == [(str(source_id),)]
    assert fresh_id != source_id
