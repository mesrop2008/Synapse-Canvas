"""The AI assistant, end to end against `FakeProvider`.

The runner records results through sessions of its own, which cannot see the
per-test transaction, so these tests use committed rows and the real session
factory, and delete what they made afterwards."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from redis.asyncio import Redis
from sqlalchemy import delete, func, select

from api.core.config import get_settings
from api.core.security import create_access_token
from api.db.session import get_engine, get_sessionmaker
from api.llm.errors import (
    ContentFilteredError,
    ContextTooLongError,
    InvalidKeyError,
    ProviderError,
    RateLimitedError,
    UpstreamUnavailableError,
)
from api.llm.fake import FakeProvider
from api.main import create_app
from api.models import AIQuery, DocumentChange, User, Workspace, WorkspaceMember
from api.models.enums import AIQueryStatus, WorkspaceRole
from api.services import documents as document_service

WORDS = ["Alpha ", "beta ", "gamma ", "delta ", "epsilon."]


def paragraph(text: str) -> dict[str, Any]:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def doc(*texts: str) -> dict[str, Any]:
    return {"type": "doc", "content": [paragraph(text) for text in texts]}


@dataclass
class Live:
    workspace_id: uuid.UUID
    document_id: uuid.UUID
    tokens: dict[WorkspaceRole, str]

    def auth(self, role: WorkspaceRole = WorkspaceRole.EDITOR) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.tokens[role]}

    @property
    def base(self) -> str:
        return f"/documents/{self.document_id}/ai/queries"


@pytest_asyncio.fixture
async def app(redis_client: Redis) -> AsyncIterator[FastAPI]:
    # The engine is cached per process but bound to the loop that first used
    # it, and each test has a loop of its own.
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    application = create_app()
    application.state.ai.provider = FakeProvider(WORDS, delay=0.01)
    try:
        yield application
    finally:
        await application.state.ai.aclose()
        await get_engine().dispose()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()


@pytest_asyncio.fixture
async def live(app: FastAPI) -> AsyncIterator[Live]:
    """A workspace with one member of each role and one document."""
    marker = uuid.uuid4().hex[:12]
    async with get_sessionmaker()() as db:
        users = {}
        for role in WorkspaceRole:
            user = User(
                email=f"{role.value}-{marker}@example.com",
                hashed_password="not-a-real-hash",
                name=role.value.title(),
            )
            db.add(user)
            users[role] = user
        await db.flush()
        workspace = Workspace(name="AI", owner_id=users[WorkspaceRole.OWNER].id)
        db.add(workspace)
        await db.flush()
        for role, user in users.items():
            db.add(
                WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role)
            )
        await db.commit()
        document = await document_service.create_document(
            db,
            workspace_id=workspace.id,
            created_by=users[WorkspaceRole.OWNER].id,
            title="Field notes",
            content=doc("The first paragraph.", "The second paragraph."),
        )
        fixture = Live(
            workspace_id=workspace.id,
            document_id=document.id,
            tokens={role: create_access_token(user.id) for role, user in users.items()},
        )

    try:
        yield fixture
    finally:
        async with get_sessionmaker()() as db:
            await db.execute(
                delete(User).where(User.email.like(f"%-{marker}@example.com"))
            )
            await db.commit()


@pytest_asyncio.fixture
async def http(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def ai_settings() -> Any:
    """Applied to the cached settings object and restored afterwards."""
    settings = get_settings()
    saved: dict[str, Any] = {}

    def _apply(**overrides: Any) -> None:
        for key, value in overrides.items():
            saved.setdefault(key, getattr(settings, key))
            setattr(settings, key, value)

    yield _apply
    for key, value in saved.items():
        setattr(settings, key, value)


@dataclass
class Stream:
    status: int = 0
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [data for name, data in self.events if name == kind]

    @property
    def text(self) -> str:
        return "".join(data["text"] for data in self.of("token"))

    @property
    def last(self) -> tuple[str, dict[str, Any]]:
        return self.events[-1]


async def read_stream(
    app: FastAPI,
    path: str,
    headers: dict[str, str],
    *,
    hang_up_after: int | None = None,
    last_event_id: str | None = None,
) -> Stream:
    """Drives the ASGI app directly: httpx's transport buffers a whole
    response, and these tests need each event as it arrives and a client
    that can hang up mid-stream."""
    stream = Stream()
    hung_up = asyncio.Event()
    requested = False
    pending = ""

    async def receive() -> dict[str, Any]:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await hung_up.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        nonlocal pending
        if message["type"] == "http.response.start":
            stream.status = message["status"]
            return
        pending += message.get("body", b"").decode()
        while "\n\n" in pending:
            block, pending = pending.split("\n\n", 1)
            fields = dict(
                line.split(": ", 1) for line in block.split("\n") if ": " in line
            )
            if "data" in fields:
                event = fields.get("event", "message")
                stream.events.append((event, json.loads(fields["data"])))
                if "id" in fields:
                    stream.ids.append(fields["id"])
        if hang_up_after is not None and len(stream.of("token")) >= hang_up_after:
            hung_up.set()

    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    if last_event_id:
        raw_headers.append((b"last-event-id", last_event_id.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test"), *raw_headers],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=15)
    return stream


async def create(
    http: AsyncClient,
    live: Live,
    role: WorkspaceRole = WorkspaceRole.EDITOR,
    **body: Any,
) -> Response:
    body.setdefault("mode", "summarize")
    return await http.post(live.base, json=body, headers=live.auth(role))


async def load_query(query_id: str) -> AIQuery:
    async with get_sessionmaker()() as db:
        query = await db.get(AIQuery, uuid.UUID(query_id))
        assert query is not None
        return query


async def settled(query_id: str) -> AIQuery:
    """The row once its generation has ended."""
    for _ in range(200):
        query = await load_query(query_id)
        if query.status is not AIQueryStatus.STREAMING:
            return query
        await asyncio.sleep(0.025)
    raise AssertionError("the generation never ended")


async def change_count(document_id: uuid.UUID) -> int:
    async with get_sessionmaker()() as db:
        count = await db.scalar(
            select(func.count()).where(DocumentChange.document_id == document_id)
        )
        return int(count or 0)


def slow_provider(app: FastAPI, chunks: int = 200, delay: float = 0.02) -> FakeProvider:
    provider = FakeProvider([f"w{i} " for i in range(chunks)], delay=delay)
    app.state.ai.provider = provider
    return provider


async def started(provider: FakeProvider) -> None:
    for _ in range(200):
        if provider.started:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the provider was never called")


# --- Streaming ---


async def test_a_query_streams_to_a_persisted_completion(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    created = await create(http, live)
    assert created.status_code == 201, created.text
    query = created.json()
    assert query["status"] == "streaming"

    stream = await read_stream(app, f"{live.base}/{query['id']}/stream", live.auth())

    assert stream.status == 200
    assert stream.text == "".join(WORDS)
    assert stream.last[0] == "done"
    assert stream.last[1]["response"] == "".join(WORDS)
    assert stream.last[1]["usage"]["model"] == "fake-1"

    row = await load_query(query["id"])
    assert row.status is AIQueryStatus.COMPLETED
    assert row.response == "".join(WORDS)
    assert row.model == "fake-1"
    assert row.prompt_tokens and row.completion_tokens
    assert row.completed_at is not None


@pytest.mark.parametrize(
    "failure",
    [
        RateLimitedError(),
        ContextTooLongError(),
        ContentFilteredError(),
        UpstreamUnavailableError(),
        InvalidKeyError(),
    ],
    ids=lambda failure: failure.code.value,
)
async def test_a_provider_failure_ends_failed_with_its_code(
    app: FastAPI, http: AsyncClient, live: Live, failure: ProviderError
) -> None:
    app.state.ai.provider = FakeProvider(WORDS, fail_with=failure, fail_after=2)
    query = (await create(http, live)).json()

    stream = await read_stream(app, f"{live.base}/{query['id']}/stream", live.auth())

    assert stream.text == "Alpha beta "
    assert stream.last == (
        "error",
        {"code": failure.code.value, "detail": failure.detail},
    )
    row = await load_query(query["id"])
    assert row.status is AIQueryStatus.FAILED
    assert row.error_code == failure.code.value
    assert row.response is None


async def test_cancel_mid_stream_stops_the_provider_and_writes_nothing(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    provider = slow_provider(app)
    query = (await create(http, live)).json()
    reader = asyncio.create_task(
        read_stream(app, f"{live.base}/{query['id']}/stream", live.auth())
    )
    await started(provider)
    await asyncio.sleep(0.1)

    cancelled = await http.post(f"{live.base}/{query['id']}/cancel", headers=live.auth())
    stream = await reader

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert 0 < len(stream.of("token")) < 200
    assert stream.last == ("cancelled", {})
    # Aborted upstream, not merely no longer read.
    assert provider.aborted == 1 and provider.finished == 0

    row = await load_query(query["id"])
    assert row.status is AIQueryStatus.CANCELLED
    assert row.response is None
    # The prompt was sent and billed, so it still counts against the budget.
    assert row.prompt_tokens
    assert await change_count(live.document_id) == 1


async def test_cancelling_a_finished_query_changes_nothing(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    query = (await create(http, live)).json()
    await read_stream(app, f"{live.base}/{query['id']}/stream", live.auth())

    cancelled = await http.post(f"{live.base}/{query['id']}/cancel", headers=live.auth())

    assert cancelled.json()["status"] == "completed"


async def test_a_reader_hanging_up_cancels_after_the_grace_period(
    app: FastAPI, http: AsyncClient, live: Live, ai_settings: Any
) -> None:
    ai_settings(ai_reconnect_grace_seconds=0.3)
    provider = slow_provider(app)
    query = (await create(http, live)).json()

    await read_stream(
        app, f"{live.base}/{query['id']}/stream", live.auth(), hang_up_after=3
    )

    assert (await settled(query["id"])).status is AIQueryStatus.CANCELLED
    assert provider.aborted == 1


async def test_a_reader_resumes_from_its_last_event_without_a_second_generation(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    provider = slow_provider(app, chunks=30, delay=0.01)
    query = (await create(http, live)).json()
    path = f"{live.base}/{query['id']}/stream"

    first = await read_stream(app, path, live.auth(), hang_up_after=5)
    second = await read_stream(app, path, live.auth(), last_event_id=first.ids[-1])

    assert first.text + second.text == "".join(f"w{i} " for i in range(30))
    assert second.last[0] == "done"
    assert provider.started == 1


async def test_a_finished_query_is_replayed_from_its_row_once_redis_forgets_it(
    app: FastAPI, http: AsyncClient, live: Live, redis_client: Redis
) -> None:
    query = (await create(http, live)).json()
    await read_stream(app, f"{live.base}/{query['id']}/stream", live.auth())
    await redis_client.delete(f"ai:{query['id']}:events")

    replay = await read_stream(app, f"{live.base}/{query['id']}/stream", live.auth())

    assert len(replay.events) == 1
    assert replay.last[0] == "done"
    assert replay.last[1]["response"] == "".join(WORDS)
