"""Live collaboration over the WebSocket endpoint.

Synchronous, unlike the rest of the suite: Starlette's WebSocket test client
drives the app from its own event loop on a worker thread. See `websocket_app`
in conftest for what that costs and why the fixtures here commit their data.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient, WebSocketDisconnect

from api.core.config import get_settings
from api.core.redis import get_redis
from api.core.security import create_access_token
from api.main import create_app
from api.models import Document, DocumentChange, User, Workspace, WorkspaceMember
from api.models.enums import WorkspaceRole
from api.realtime.hub import DocumentHub
from api.services import documents as document_service
from api.services import presence
from tests.conftest import _TEST_DATABASE_URL, new_redis, websocket_app


def step(text: str, at: int = 1) -> dict[str, Any]:
    """A ProseMirror replace step. The server stores steps without interpreting
    them, so only the shape matters here."""
    return {
        "stepType": "replace",
        "from": at,
        "to": at,
        "slice": {"content": [{"type": "text", "text": text}]},
    }


def doc(text: str) -> dict[str, Any]:
    return {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]}
        ],
    }


def edit(base_version: int, text: str) -> dict[str, Any]:
    return {
        "type": "edit",
        "base_version": base_version,
        "operation": {"steps": [step(text)], "doc": doc(text)},
    }


@dataclass
class Fixture:
    """Committed rows, so the app's own sessions can see them."""

    workspace_id: uuid.UUID
    document_id: uuid.UUID
    owner_id: uuid.UUID
    editor_id: uuid.UUID
    viewer_id: uuid.UUID
    owner_token: str
    editor_token: str
    viewer_token: str

    def auth(self, token: str) -> dict[str, str]:
        return {"Authorization": "Bearer " + token}


@pytest.fixture
def live() -> Iterator[Fixture]:
    """One document in a workspace holding an owner, an editor and a viewer."""
    marker = uuid.uuid4().hex[:12]

    async def _seed() -> Fixture:
        engine = create_async_engine(_TEST_DATABASE_URL, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                users = {}
                for role in (
                    WorkspaceRole.OWNER,
                    WorkspaceRole.EDITOR,
                    WorkspaceRole.VIEWER,
                ):
                    user = User(
                        email=f"{role.value}-{marker}@example.com",
                        hashed_password="not-a-real-hash",
                        name=role.value.title(),
                    )
                    session.add(user)
                    users[role] = user
                await session.flush()

                workspace = Workspace(
                    name="Live", owner_id=users[WorkspaceRole.OWNER].id
                )
                session.add(workspace)
                await session.flush()

                for role, user in users.items():
                    session.add(
                        WorkspaceMember(
                            workspace_id=workspace.id, user_id=user.id, role=role
                        )
                    )
                await session.commit()

                document = await document_service.create_document(
                    session,
                    workspace_id=workspace.id,
                    created_by=users[WorkspaceRole.OWNER].id,
                    title="Live document",
                )

                return Fixture(
                    workspace_id=workspace.id,
                    document_id=document.id,
                    owner_id=users[WorkspaceRole.OWNER].id,
                    editor_id=users[WorkspaceRole.EDITOR].id,
                    viewer_id=users[WorkspaceRole.VIEWER].id,
                    owner_token=create_access_token(users[WorkspaceRole.OWNER].id),
                    editor_token=create_access_token(users[WorkspaceRole.EDITOR].id),
                    viewer_token=create_access_token(users[WorkspaceRole.VIEWER].id),
                )
        finally:
            await engine.dispose()

    async def _teardown() -> None:
        engine = create_async_engine(_TEST_DATABASE_URL, poolclass=NullPool)
        try:
            async with AsyncSession(engine) as session:
                await session.execute(
                    delete(User).where(User.email.like(f"%-{marker}@example.com"))
                )
                await session.commit()
        finally:
            await engine.dispose()

    # Its own loop, finished before the TestClient's starts.
    fixture = asyncio.run(_seed())
    try:
        yield fixture
    finally:
        asyncio.run(_teardown())


def ticket_for(client: TestClient, fixture: Fixture, token: str) -> str:
    response = client.post(
        f"/documents/{fixture.document_id}/ws-ticket", headers=fixture.auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["ticket"]


def socket_url(fixture: Fixture, ticket: str) -> str:
    return f"/ws/documents/{fixture.document_id}?ticket={ticket}"


async def _document_state(document_id: uuid.UUID) -> tuple[int, dict[str, Any]]:
    engine = create_async_engine(_TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            row = (
                await session.execute(
                    select(Document.version, Document.content).where(
                        Document.id == document_id
                    )
                )
            ).one()
            return int(row[0]), row[1]
    finally:
        await engine.dispose()


async def _logged_versions(document_id: uuid.UUID) -> list[int]:
    engine = create_async_engine(_TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            rows = await session.execute(
                select(DocumentChange.version)
                .where(DocumentChange.document_id == document_id)
                .order_by(DocumentChange.version)
            )
            return [int(v) for v in rows.scalars().all()]
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# Tickets
# --------------------------------------------------------------------------- #


def test_a_ticket_opens_the_socket_once(live: Fixture) -> None:
    with websocket_app() as client:
        ticket = ticket_for(client, live, live.owner_token)

        with client.websocket_connect(socket_url(live, ticket)) as websocket:
            init = websocket.receive_json()
            assert init["type"] == "init"
            assert init["version"] == 1
            assert init["peers"] == []
            assert init["you"]["user_id"] == str(live.owner_id)
            assert init["you"]["role"] == "owner"

        # Redeemed and deleted on the first connect, so the second is refused.
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(socket_url(live, ticket)):
                pass


def test_a_ticket_expires(live: Fixture) -> None:
    settings = get_settings()
    original = settings.ws_ticket_ttl_seconds
    settings.ws_ticket_ttl_seconds = 1
    try:
        with websocket_app() as client:
            ticket = ticket_for(client, live, live.owner_token)
            time.sleep(1.2)  # the TTL is Redis's, so it has to actually elapse

            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(socket_url(live, ticket)):
                    pass
    finally:
        settings.ws_ticket_ttl_seconds = original


def test_the_socket_refuses_a_missing_or_foreign_ticket(live: Fixture) -> None:
    with websocket_app() as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/documents/{live.document_id}"):
                pass

        # A ticket is bound to one document.
        ticket = ticket_for(client, live, live.owner_token)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/ws/documents/{uuid.uuid4()}?ticket={ticket}"
            ):
                pass


def test_a_non_member_cannot_mint_a_ticket(live: Fixture) -> None:
    with websocket_app() as client:
        stranger = create_access_token(uuid.uuid4())
        response = client.post(
            f"/documents/{live.document_id}/ws-ticket", headers=live.auth(stranger)
        )
        assert response.status_code == 401

        response = client.post(f"/documents/{live.document_id}/ws-ticket")
        assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Edits
# --------------------------------------------------------------------------- #


def test_an_edit_is_acked_logged_and_broadcast(live: Fixture) -> None:
    with websocket_app() as client:
        author = ticket_for(client, live, live.editor_token)
        watcher = ticket_for(client, live, live.viewer_token)

        with client.websocket_connect(socket_url(live, watcher)) as viewer:
            assert viewer.receive_json()["type"] == "init"

            with client.websocket_connect(socket_url(live, author)) as editor:
                assert editor.receive_json()["type"] == "init"
                assert viewer.receive_json()["type"] == "presence"

                editor.send_json(edit(1, "Hello"))
                assert editor.receive_json() == {"type": "edit_ack", "version": 2}

                relayed = viewer.receive_json()
                assert relayed["type"] == "edit"
                assert relayed["version"] == 2
                assert relayed["user_id"] == str(live.editor_id)
                # The snapshot is not re-sent to peers; they replay the steps.
                assert relayed["operation"] == {"steps": [step("Hello")]}

    version, content = asyncio.run(_document_state(live.document_id))
    assert version == 2
    assert content == doc("Hello")
    assert asyncio.run(_logged_versions(live.document_id)) == [1, 2]


def test_a_viewer_cannot_edit(live: Fixture) -> None:
    with websocket_app() as client:
        ticket = ticket_for(client, live, live.viewer_token)

        with client.websocket_connect(socket_url(live, ticket)) as websocket:
            assert websocket.receive_json()["type"] == "init"

            websocket.send_json(edit(1, "Sneaky"))
            refusal = websocket.receive_json()
            assert refusal["type"] == "error"
            assert refusal["code"] == "forbidden"

            # Still connected, still receiving: read-only, not disconnected.
            websocket.send_json({"type": "ping"})
            assert websocket.receive_json() == {"type": "pong"}

    assert asyncio.run(_document_state(live.document_id))[0] == 1
    assert asyncio.run(_logged_versions(live.document_id)) == [1]


def test_a_stale_edit_is_rejected_and_writes_nothing(live: Fixture) -> None:
    with websocket_app() as client:
        ticket = ticket_for(client, live, live.editor_token)

        with client.websocket_connect(socket_url(live, ticket)) as websocket:
            assert websocket.receive_json()["type"] == "init"

            websocket.send_json(edit(1, "First"))
            assert websocket.receive_json()["version"] == 2

            # Computed against version 1, which is no longer the head.
            websocket.send_json(edit(1, "Stale"))
            rejection = websocket.receive_json()
            assert rejection["type"] == "rejected"
            assert rejection["server_version"] == 2
            assert rejection["content"] == doc("First")

    version, content = asyncio.run(_document_state(live.document_id))
    assert version == 2
    assert content == doc("First")
    # The rejected edit left no row behind.
    assert asyncio.run(_logged_versions(live.document_id)) == [1, 2]


def test_two_edits_at_the_same_version_leave_one_winner(live: Fixture) -> None:
    """Both sockets are told version 1 and both write against it. One is
    acknowledged, the other is rejected, and the log holds one row per
    version -- which the unique constraint would refuse to let it not."""
    with websocket_app() as client:
        first = ticket_for(client, live, live.owner_token)
        second = ticket_for(client, live, live.editor_token)

        with client.websocket_connect(socket_url(live, first)) as a:
            assert a.receive_json()["type"] == "init"

            with client.websocket_connect(socket_url(live, second)) as b:
                assert b.receive_json()["type"] == "init"
                assert a.receive_json()["type"] == "presence"

                # Queued back to back, before either reply is read.
                a.send_json(edit(1, "From A"))
                b.send_json(edit(1, "From B"))

                replies = {}
                for name, socket in (("a", a), ("b", b)):
                    while True:
                        message = socket.receive_json()
                        if message["type"] in {"edit_ack", "rejected"}:
                            replies[name] = message
                            break

    assert sorted(m["type"] for m in replies.values()) == ["edit_ack", "rejected"]

    versions = asyncio.run(_logged_versions(live.document_id))
    assert versions == [1, 2]
    assert len(versions) == len(set(versions))
    assert asyncio.run(_document_state(live.document_id))[0] == 2


def test_the_http_patch_shares_the_log_with_the_socket(live: Fixture) -> None:
    """Part 2's PATCH is not a second write path: it takes the same lock, lands
    in the same log, and its version is announced to anyone connected."""
    with websocket_app() as client:
        ticket = ticket_for(client, live, live.editor_token)

        with client.websocket_connect(socket_url(live, ticket)) as websocket:
            assert websocket.receive_json()["type"] == "init"

            response = client.patch(
                f"/workspaces/{live.workspace_id}/documents/{live.document_id}",
                json={"version": 1, "title": "Renamed"},
                headers=live.auth(live.owner_token),
            )
            assert response.status_code == 200, response.text
            assert response.json()["version"] == 2

            announced = websocket.receive_json()
            assert announced["type"] == "edit"
            assert announced["version"] == 2
            assert announced["operation"] == {"title": "Renamed"}

    assert asyncio.run(_logged_versions(live.document_id)) == [1, 2]


def test_deleting_a_document_tells_everyone_who_has_it_open(live: Fixture) -> None:
    """An editor may delete, and the people reading it have to hear about it
    from the server rather than from their next edit failing."""
    with websocket_app() as client:
        ticket = ticket_for(client, live, live.owner_token)

        with client.websocket_connect(socket_url(live, ticket)) as websocket:
            assert websocket.receive_json()["type"] == "init"

            response = client.delete(
                f"/workspaces/{live.workspace_id}/documents/{live.document_id}",
                headers=live.auth(live.editor_token),  # an editor, not the owner
            )
            assert response.status_code == 204

            assert websocket.receive_json() == {
                "type": "deleted",
                "user_id": str(live.editor_id),
            }

        # And the door is shut behind it: no new socket can be opened.
        response = client.post(
            f"/documents/{live.document_id}/ws-ticket",
            headers=live.auth(live.owner_token),
        )
        assert response.status_code == 404


def test_deleting_a_document_takes_its_change_log_with_it(live: Fixture) -> None:
    with websocket_app() as client:
        ticket = ticket_for(client, live, live.editor_token)
        with client.websocket_connect(socket_url(live, ticket)) as websocket:
            assert websocket.receive_json()["type"] == "init"
            websocket.send_json(edit(1, "Doomed"))
            assert websocket.receive_json()["type"] == "edit_ack"

        assert asyncio.run(_logged_versions(live.document_id)) == [1, 2]

        response = client.delete(
            f"/workspaces/{live.workspace_id}/documents/{live.document_id}",
            headers=live.auth(live.owner_token),
        )
        assert response.status_code == 204

    # ON DELETE CASCADE: the log is a concurrency mechanism, not an archive.
    assert asyncio.run(_logged_versions(live.document_id)) == []


# --------------------------------------------------------------------------- #
# Presence
# --------------------------------------------------------------------------- #


def test_presence_arrives_on_connect_and_clears_on_disconnect(live: Fixture) -> None:
    with websocket_app() as client:
        first = ticket_for(client, live, live.owner_token)
        second = ticket_for(client, live, live.editor_token)

        with client.websocket_connect(socket_url(live, first)) as a:
            assert a.receive_json()["peers"] == []

            with client.websocket_connect(socket_url(live, second)) as b:
                # B sees A already there; A is told about B.
                assert [p["user_id"] for p in b.receive_json()["peers"]] == [
                    str(live.owner_id)
                ]

                arrival = a.receive_json()
                assert arrival["type"] == "presence"
                assert arrival["user_id"] == str(live.editor_id)
                assert arrival["color"].startswith("#")
                assert arrival["anchor"] is None

                b.send_json({"type": "cursor", "anchor": 12, "head": 18})
                moved = a.receive_json()
                assert (moved["anchor"], moved["head"]) == (12, 18)

                held = client.portal.call(
                    presence.peers, get_redis(), live.document_id
                )
                assert {str(p.user_id) for p in held} == {
                    str(live.owner_id),
                    str(live.editor_id),
                }

            assert a.receive_json() == {
                "type": "peer_left",
                "user_id": str(live.editor_id),
            }

            remaining = client.portal.call(
                presence.peers, get_redis(), live.document_id
            )
            assert [str(p.user_id) for p in remaining] == [str(live.owner_id)]


def test_a_lapsed_entry_is_swept_and_announced(live: Fixture) -> None:
    """The ungraceful-drop case: nothing ran `leave`, so the entry has to age
    out and the next peer's heartbeat has to notice."""
    with websocket_app() as client:
        ticket = ticket_for(client, live, live.owner_token)

        with client.websocket_connect(socket_url(live, ticket)) as websocket:
            assert websocket.receive_json()["type"] == "init"

            ghost = presence.Peer(
                user_id=live.viewer_id,
                name="Ghost",
                color="#000000",
                anchor=None,
                head=None,
                connection_id="gone",
            )
            # Written straight into Redis with a TTL already in the past.
            client.portal.call(
                get_redis().hset,
                "presence:" + str(live.document_id),
                str(live.viewer_id),
                '{"name": "Ghost", "color": "#000000", "anchor": null, '
                '"head": null, "connection_id": "gone", "expires_at": 1}',
            )
            assert ghost.user_id not in {
                p.user_id
                for p in client.portal.call(
                    presence.peers, get_redis(), live.document_id
                )
            }

            websocket.send_json({"type": "ping"})
            # The pong goes straight back down the socket while the sweep takes
            # the Redis round trip, so the two can arrive either way round.
            received = [websocket.receive_json(), websocket.receive_json()]
            assert {"type": "pong"} in received
            assert {
                "type": "peer_left",
                "user_id": str(live.viewer_id),
            } in received


# --------------------------------------------------------------------------- #
# Across workers
# --------------------------------------------------------------------------- #


def test_a_write_on_one_worker_reaches_a_connection_on_another(
    live: Fixture,
) -> None:
    """The thing Redis is there for.

    Two apps, each with its own hub and its own Redis client -- so two
    independent registries and two independent connections to the server, which
    is what separates one Uvicorn worker from the next. The socket is held by
    the first; the write goes to the second, which has never heard of it.

    Both run on one event loop here, because the test client owns it. What is
    being proved is that the registries are separate and the message crossed
    between them through Redis, and that part is unaffected.
    """
    with websocket_app() as worker_a:
        worker_b = create_app()
        redis_b = new_redis()
        worker_b.state.hub = DocumentHub(redis_b)

        async def patch_through_worker_b() -> int:
            transport = ASGITransport(app=worker_b)
            async with AsyncClient(
                transport=transport, base_url="http://worker-b"
            ) as http:
                response = await http.patch(
                    f"/workspaces/{live.workspace_id}/documents/{live.document_id}",
                    json={"version": 1, "content": doc("Written on B")},
                    headers=live.auth(live.editor_token),
                )
                assert response.status_code == 200, response.text
                return int(response.json()["version"])

        ticket = ticket_for(worker_a, live, live.owner_token)
        with worker_a.websocket_connect(socket_url(live, ticket)) as websocket:
            assert websocket.receive_json()["type"] == "init"
            assert worker_a.portal.call(patch_through_worker_b) == 2

            relayed = websocket.receive_json()
            assert relayed["type"] == "edit"
            assert relayed["version"] == 2
            assert relayed["operation"] == {"replace": doc("Written on B")}

        worker_a.portal.call(worker_b.state.hub.aclose)
        worker_a.portal.call(redis_b.aclose)


# --------------------------------------------------------------------------- #
# Hygiene
# --------------------------------------------------------------------------- #


def test_an_oversized_message_closes_the_socket(live: Fixture) -> None:
    settings = get_settings()
    original = settings.ws_max_message_bytes
    settings.ws_max_message_bytes = 512
    try:
        with websocket_app() as client:
            ticket = ticket_for(client, live, live.editor_token)

            with client.websocket_connect(socket_url(live, ticket)) as websocket:
                assert websocket.receive_json()["type"] == "init"
                websocket.send_json(edit(1, "x" * 2000))
                with pytest.raises(WebSocketDisconnect):
                    websocket.receive_json()
    finally:
        settings.ws_max_message_bytes = original

    assert asyncio.run(_document_state(live.document_id))[0] == 1


def test_too_many_edits_closes_the_socket(live: Fixture) -> None:
    settings = get_settings()
    original = settings.ws_edit_rate_limit
    settings.ws_edit_rate_limit = 2
    try:
        with websocket_app() as client:
            ticket = ticket_for(client, live, live.editor_token)

            with client.websocket_connect(socket_url(live, ticket)) as websocket:
                assert websocket.receive_json()["type"] == "init"

                for version in (1, 2):
                    websocket.send_json(edit(version, f"Edit {version}"))
                    assert websocket.receive_json()["type"] == "edit_ack"

                websocket.send_json(edit(3, "One too many"))
                with pytest.raises(WebSocketDisconnect):
                    websocket.receive_json()
    finally:
        settings.ws_edit_rate_limit = original

    assert asyncio.run(_document_state(live.document_id))[0] == 3


def test_a_malformed_frame_is_reported_without_closing(live: Fixture) -> None:
    with websocket_app() as client:
        ticket = ticket_for(client, live, live.editor_token)

        with client.websocket_connect(socket_url(live, ticket)) as websocket:
            assert websocket.receive_json()["type"] == "init"

            websocket.send_text("not json")
            assert websocket.receive_json()["code"] == "malformed"

            websocket.send_json({"type": "edit", "base_version": 1})
            assert websocket.receive_json()["code"] == "malformed"

            websocket.send_json({"type": "ping"})
            assert websocket.receive_json() == {"type": "pong"}


def test_a_disconnect_clears_the_registry(live: Fixture) -> None:
    with websocket_app() as client:
        ticket = ticket_for(client, live, live.owner_token)
        hub = client.app.state.hub  # type: ignore[attr-defined]

        with client.websocket_connect(socket_url(live, ticket)) as websocket:
            assert websocket.receive_json()["type"] == "init"
            assert len(hub.local_connections(live.document_id)) == 1

        # The socket's own cleanup runs as the handler unwinds, so give the
        # app loop a moment to finish it.
        deadline = time.monotonic() + 2
        while hub.local_connections(live.document_id) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert hub.local_connections(live.document_id) == frozenset()


def test_the_change_log_is_append_only_per_version(live: Fixture) -> None:
    """Whatever the sockets do, the database will not hold two rows claiming the
    same version of a document."""
    with websocket_app() as client:
        ticket = ticket_for(client, live, live.editor_token)
        with client.websocket_connect(socket_url(live, ticket)) as websocket:
            assert websocket.receive_json()["type"] == "init"
            for version in (1, 2, 3):
                websocket.send_json(edit(version, f"Edit {version}"))
                assert websocket.receive_json()["version"] == version + 1

    async def _duplicates() -> int:
        engine = create_async_engine(_TEST_DATABASE_URL, poolclass=NullPool)
        try:
            async with AsyncSession(engine) as session:
                duplicated = (
                    select(DocumentChange.version)
                    .where(DocumentChange.document_id == live.document_id)
                    .group_by(DocumentChange.version)
                    .having(func.count() > 1)
                )
                return len((await session.execute(duplicated)).all())
        finally:
            await engine.dispose()

    assert asyncio.run(_logged_versions(live.document_id)) == [1, 2, 3, 4]
    assert asyncio.run(_duplicates()) == 0
