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

from api.core import i18n
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
from tests.test_ai_gemini import GeminiStub
from tests.test_ai_gemini import chunk as gemini_chunk

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


# --- Access ---


async def test_a_viewer_cannot_start_a_query(http: AsyncClient, live: Live) -> None:
    refused = await create(http, live, WorkspaceRole.VIEWER)

    assert refused.status_code == 403
    assert refused.json()["code"] == "workspace.role_too_low"
    async with get_sessionmaker()() as db:
        count = await db.scalar(
            select(func.count()).where(AIQuery.document_id == live.document_id)
        )
    assert count == 0


async def test_a_non_member_cannot_see_the_document(
    http: AsyncClient, live: Live
) -> None:
    stranger = {"Authorization": "Bearer " + create_access_token(uuid.uuid4())}
    response = await http.post(live.base, json={"mode": "summarize"}, headers=stranger)
    assert response.status_code == 401

    async with get_sessionmaker()() as db:
        outsider = User(
            email=f"outsider-{uuid.uuid4().hex[:12]}@example.com",
            hashed_password="not-a-real-hash",
            name="Outsider",
        )
        db.add(outsider)
        await db.commit()
    try:
        headers = {"Authorization": "Bearer " + create_access_token(outsider.id)}
        response = await http.post(live.base, json={"mode": "summarize"}, headers=headers)
        assert response.status_code == 404
    finally:
        async with get_sessionmaker()() as db:
            await db.execute(delete(User).where(User.id == outsider.id))
            await db.commit()


async def test_a_query_is_visible_only_to_its_author(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    query = (await create(http, live)).json()
    owner = live.auth(WorkspaceRole.OWNER)

    cancel = await http.post(f"{live.base}/{query['id']}/cancel", headers=owner)
    assert cancel.status_code == 404
    history = await http.get(live.base, headers=owner)
    assert history.json()["items"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "ask"},
        {"mode": "rewrite", "instruction": "Shorter"},
        {"mode": "rewrite", "selection_from": 5, "selection_to": 5},
        {"mode": "rewrite", "selection_from": 9, "selection_to": 5},
        {"mode": "continue", "selection_to": 10_000},
        {"mode": "summarize", "instruction": "x" * 2001},
    ],
)
async def test_an_impossible_request_is_refused_before_it_costs_anything(
    app: FastAPI, http: AsyncClient, live: Live, body: dict[str, Any]
) -> None:
    response = await http.post(live.base, json=body, headers=live.auth())

    assert response.status_code == 422, response.text
    assert app.state.ai.provider.started == 0


# --- Limits ---


async def test_the_daily_token_ceiling_returns_429(
    app: FastAPI, http: AsyncClient, live: Live, ai_settings: Any
) -> None:
    ai_settings(ai_daily_token_limit=50)

    refused = await create(http, live)

    assert refused.status_code == 429
    assert refused.json()["code"] == "ai.daily_limit"
    assert "50" in refused.json()["detail"]
    assert int(refused.headers["Retry-After"]) > 0
    assert app.state.ai.provider.started == 0


async def test_usage_is_recorded_when_a_query_ends_and_counts_against_the_day(
    app: FastAPI, http: AsyncClient, live: Live, ai_settings: Any
) -> None:
    query = (await create(http, live)).json()
    await read_stream(app, f"{live.base}/{query['id']}/stream", live.auth())
    row = await load_query(query["id"])
    spent = (row.prompt_tokens or 0) + (row.completion_tokens or 0)

    usage = await http.get(
        f"/workspaces/{live.workspace_id}/usage", headers=live.auth(WorkspaceRole.VIEWER)
    )
    assert usage.status_code == 200
    assert usage.json()["tokens_used"] == spent
    assert usage.json()["tokens_remaining"] == 200_000 - spent

    # Room for less than another prompt of the same size.
    ai_settings(ai_daily_token_limit=spent + 10)
    refused = await create(http, live)
    assert refused.status_code == 429
    assert refused.json()["code"] == "ai.daily_limit"


async def test_concurrent_generations_are_capped_per_workspace(
    app: FastAPI, http: AsyncClient, live: Live, ai_settings: Any
) -> None:
    ai_settings(ai_max_concurrent_queries=1)
    slow_provider(app)
    first = (await create(http, live)).json()

    refused = await create(http, live, WorkspaceRole.OWNER)
    assert refused.status_code == 429
    assert refused.json()["code"] == "ai.concurrency_limit"

    await http.post(f"{live.base}/{first['id']}/cancel", headers=live.auth())
    assert (await create(http, live, WorkspaceRole.OWNER)).status_code == 201


async def test_two_requests_cannot_both_take_the_last_slot(
    app: FastAPI, http: AsyncClient, live: Live, ai_settings: Any
) -> None:
    ai_settings(ai_max_concurrent_queries=1)
    slow_provider(app)

    responses = await asyncio.gather(*(create(http, live) for _ in range(4)))

    assert sorted(r.status_code for r in responses) == [201, 429, 429, 429]


# --- History ---


async def test_history_pages_newest_first(
    app: FastAPI, http: AsyncClient, live: Live, ai_settings: Any
) -> None:
    ai_settings(ai_max_concurrent_queries=0)
    ids = [(await create(http, live, instruction=f"q{i}")).json()["id"] for i in range(3)]

    first = (await http.get(live.base, params={"limit": 2}, headers=live.auth())).json()
    second = (
        await http.get(
            live.base,
            params={"limit": 2, "cursor": first["next_cursor"]},
            headers=live.auth(),
        )
    ).json()

    assert [item["id"] for item in first["items"]] == ids[:0:-1]
    assert [item["id"] for item in second["items"]] == ids[:1]
    assert second["next_cursor"] is None

    bad = await http.get(live.base, params={"cursor": "nonsense"}, headers=live.auth())
    assert bad.status_code == 422


# --- Prompt assembly through the API ---


async def test_a_long_document_is_cut_from_the_middle_keeping_the_selection(
    app: FastAPI, http: AsyncClient, live: Live, ai_settings: Any
) -> None:
    ai_settings(ai_context_token_limit=200)
    selected = "Keep every word of this sentence."
    content = doc("The opening line.", "filler " * 2000, selected, "trailing " * 2000)
    async with get_sessionmaker()() as db:
        await document_service.update_document(
            db,
            workspace_id=live.workspace_id,
            document_id=live.document_id,
            expected_version=1,
            content=content,
        )
    start = (len("The opening line.") + 2) + (len("filler " * 2000) + 2) + 1
    provider: FakeProvider = app.state.ai.provider

    created = await create(
        http,
        live,
        mode="rewrite",
        instruction="Tighten it",
        selection_from=start,
        selection_to=start + len(selected),
    )

    assert created.status_code == 201, created.text
    await started(provider)
    prompt = provider.requests[0].user
    assert f"<selection>\n{selected}\n</selection>" in prompt
    assert "The opening line." in prompt
    assert "[…]" in prompt
    assert len(prompt) < 200 * 3 + 1000


# --- Apply ---


async def completed(
    app: FastAPI, http: AsyncClient, live: Live, **body: Any
) -> dict[str, Any]:
    query = (await create(http, live, **body)).json()
    stream = await read_stream(app, f"{live.base}/{query['id']}/stream", live.auth())
    assert stream.last[0] == "done", stream.events
    return query


async def apply(http: AsyncClient, live: Live, query_id: str, **body: Any) -> Response:
    path = f"{live.base}/{query_id}/apply"
    return await http.post(path, json=body, headers=live.auth())


async def test_apply_writes_one_change_through_the_locked_path(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    query = await completed(app, http, live)

    applied = await apply(http, live, query["id"], version=1)

    assert applied.status_code == 200, applied.text
    assert applied.json()["version"] == 2
    assert applied.json()["content"] == doc(
        "The first paragraph.", "The second paragraph.", "".join(WORDS)
    )
    async with get_sessionmaker()() as db:
        change = await db.scalar(
            select(DocumentChange).where(
                DocumentChange.document_id == live.document_id,
                DocumentChange.version == 2,
            )
        )
    assert change is not None
    assert change.base_version == 1
    assert change.operation["ai_query_id"] == query["id"]
    assert [step["stepType"] for step in change.operation["steps"]] == ["replace"]
    assert await change_count(live.document_id) == 2


async def test_apply_against_a_stale_version_is_refused_like_any_edit(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    query = await completed(app, http, live)
    async with get_sessionmaker()() as db:
        await document_service.update_document(
            db,
            workspace_id=live.workspace_id,
            document_id=live.document_id,
            expected_version=1,
            title="Renamed meanwhile",
        )

    refused = await apply(http, live, query["id"], version=1)

    assert refused.status_code == 409
    assert refused.json()["code"] == "document.stale"
    assert refused.json()["current"]["version"] == 2
    assert await change_count(live.document_id) == 2


async def test_a_retried_apply_cannot_insert_twice(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    query = await completed(app, http, live)

    first = await apply(http, live, query["id"], version=1)
    again = await apply(http, live, query["id"], version=1)

    assert first.status_code == 200
    assert again.status_code == 409
    assert await change_count(live.document_id) == 2


async def test_rewrite_replaces_only_the_selection(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    app.state.ai.provider = FakeProvider(["A ", "better ", "one"])
    # "The first paragraph." starts at position 1; "first" spans 5 to 10.
    query = await completed(
        app, http, live, mode="rewrite", selection_from=5, selection_to=10
    )

    applied = await apply(http, live, query["id"], version=1)

    assert applied.json()["content"] == doc(
        "The A better one paragraph.", "The second paragraph."
    )


async def test_apply_follows_the_range_the_client_mapped(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    app.state.ai.provider = FakeProvider(["second"])
    query = await completed(
        app, http, live, mode="rewrite", selection_from=5, selection_to=10
    )
    # A peer typed "Oh. " at the start meanwhile, moving "first" four along.
    async with get_sessionmaker()() as db:
        await document_service.update_document(
            db,
            workspace_id=live.workspace_id,
            document_id=live.document_id,
            expected_version=1,
            content=doc("Oh. The first paragraph.", "The second paragraph."),
        )

    applied = await apply(
        http, live, query["id"], version=2, selection_from=9, selection_to=14
    )

    assert applied.json()["content"] == doc(
        "Oh. The second paragraph.", "The second paragraph."
    )


async def test_only_a_completed_response_can_be_applied(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    app.state.ai.provider = FakeProvider(WORDS, fail_with=RateLimitedError())
    query = await create(http, live)
    await read_stream(app, f"{live.base}/{query.json()['id']}/stream", live.auth())

    refused = await apply(http, live, query.json()["id"], version=1)

    assert refused.status_code == 409
    assert refused.json()["code"] == "ai.query_not_completed"
    assert await change_count(live.document_id) == 1


async def test_a_viewer_cannot_apply(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    query = await completed(app, http, live)

    refused = await http.post(
        f"{live.base}/{query['id']}/apply",
        json={"version": 1},
        headers=live.auth(WorkspaceRole.VIEWER),
    )

    assert refused.status_code == 403
    assert await change_count(live.document_id) == 1


# --- Through the real SDK ---


async def test_cancel_reaches_geminis_http_connection(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    """The runner cancels the task awaiting the SDK, which is what closes the
    response; closing the SDK's stream alone would leave it to the GC."""
    stub = GeminiStub([gemini_chunk(f"w{i} ") for i in range(200)], delay=0.02)
    app.state.ai.provider = stub.provider()
    query = (await create(http, live)).json()
    reader = asyncio.create_task(
        read_stream(app, f"{live.base}/{query['id']}/stream", live.auth())
    )
    for _ in range(200):
        if stub.served >= 3:
            break
        await asyncio.sleep(0.01)

    cancelled = await http.post(f"{live.base}/{query['id']}/cancel", headers=live.auth())
    stream = await reader

    assert cancelled.json()["status"] == "cancelled"
    assert stub.closed_early == 1
    assert stub.served < 200
    assert stream.last == ("cancelled", {})


# --- Language ---


async def test_a_russian_client_gets_russian_wording(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    provider = FakeProvider()
    app.state.ai.provider = provider
    headers = {**live.auth(), "Accept-Language": "ru"}

    created = await http.post(live.base, json={"mode": "summarize"}, headers=headers)
    query = created.json()
    stream = await read_stream(app, f"{live.base}/{query['id']}/stream", live.auth())

    assert provider.requests[0].system == i18n.text("ru", "ai.prompt.system")
    assert stream.text == i18n.text("ru", "ai.fake.reply")
