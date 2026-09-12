"""Document CRUD, the permission boundaries around it, and the 409 path.

The 409 tests are the point of the file: a stale PATCH must be refused *and*
leave the row alone, and two simultaneous PATCHes against one version must not
both be applied.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from api.models import User, Workspace
from api.services import documents as document_service
from tests.conftest import _TEST_DATABASE_URL, TestUser

PARAGRAPH = {
    "type": "doc",
    "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]}
    ],
}


def docs_url(workspace: dict[str, Any], document_id: str | None = None) -> str:
    base = "/workspaces/%s/documents" % workspace["id"]
    return base if document_id is None else "%s/%s" % (base, document_id)


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


async def test_create_document_defaults_to_an_empty_prosemirror_doc(
    client: AsyncClient, owner: TestUser, shared_workspace: dict[str, Any]
) -> None:
    response = await client.post(
        docs_url(shared_workspace),
        json={"title": "  Untitled  "},
        headers=owner.headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "Untitled"  # NonEmptyName strips
    assert body["content"] == {"type": "doc", "content": []}
    assert body["version"] == 1
    assert body["created_by"] == str(owner.id)
    assert body["workspace_id"] == shared_workspace["id"]


async def test_create_document_stores_the_supplied_content(
    client: AsyncClient, editor: TestUser, shared_workspace: dict[str, Any]
) -> None:
    response = await client.post(
        docs_url(shared_workspace),
        json={"title": "Notes", "content": PARAGRAPH},
        headers=editor.headers,
    )

    assert response.status_code == 201, response.text
    assert response.json()["content"] == PARAGRAPH


async def test_list_omits_content_bodies(
    client: AsyncClient,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
) -> None:
    response = await client.get(docs_url(shared_workspace), headers=owner.headers)

    assert response.status_code == 200
    listed = response.json()
    assert [d["id"] for d in listed] == [document["id"]]
    assert "content" not in listed[0]
    assert listed[0]["version"] == 1


async def test_list_is_newest_edited_first(
    client: AsyncClient, owner: TestUser, shared_workspace: dict[str, Any]
) -> None:
    created = {}
    for title in ("A", "B", "C"):
        response = await client.post(
            docs_url(shared_workspace), json={"title": title}, headers=owner.headers
        )
        created[title] = response.json()["id"]

    # Edit in a deliberately different order from creation, so the result can
    # only be explained by updated_at.
    for title in ("C", "A", "B"):
        patched = await client.patch(
            docs_url(shared_workspace, created[title]),
            json={"version": 1, "content": PARAGRAPH},
            headers=owner.headers,
        )
        assert patched.status_code == 200, patched.text

    listed = (
        await client.get(docs_url(shared_workspace), headers=owner.headers)
    ).json()
    assert [d["title"] for d in listed] == ["B", "A", "C"]


async def test_get_returns_content_and_version(
    client: AsyncClient,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
) -> None:
    response = await client.get(
        docs_url(shared_workspace, document["id"]), headers=owner.headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == {"type": "doc", "content": []}
    assert body["version"] == 1


async def test_patch_content_increments_the_version(
    client: AsyncClient,
    editor: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
) -> None:
    response = await client.patch(
        docs_url(shared_workspace, document["id"]),
        json={"version": document["version"], "content": PARAGRAPH},
        headers=editor.headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == document["version"] + 1
    assert body["content"] == PARAGRAPH
    assert body["title"] == document["title"]
    assert body["updated_at"] > document["updated_at"]


async def test_patch_title_alone_leaves_content_intact(
    client: AsyncClient,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
) -> None:
    await client.patch(
        docs_url(shared_workspace, document["id"]),
        json={"version": 1, "content": PARAGRAPH},
        headers=owner.headers,
    )
    response = await client.patch(
        docs_url(shared_workspace, document["id"]),
        json={"version": 2, "title": "Renamed"},
        headers=owner.headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Renamed"
    assert body["version"] == 3
    assert body["content"] == PARAGRAPH


async def test_successive_patches_each_bump_the_version(
    client: AsyncClient,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
) -> None:
    version = document["version"]
    for expected in (2, 3, 4):
        response = await client.patch(
            docs_url(shared_workspace, document["id"]),
            json={"version": version, "content": PARAGRAPH},
            headers=owner.headers,
        )
        assert response.status_code == 200, response.text
        version = response.json()["version"]
        assert version == expected


async def test_delete_removes_the_document(
    client: AsyncClient,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
) -> None:
    deleted = await client.delete(
        docs_url(shared_workspace, document["id"]), headers=owner.headers
    )
    assert deleted.status_code == 204

    missing = await client.get(
        docs_url(shared_workspace, document["id"]), headers=owner.headers
    )
    assert missing.status_code == 404
    assert (
        await client.get(docs_url(shared_workspace), headers=owner.headers)
    ).json() == []


async def test_unknown_document_id_is_404(
    client: AsyncClient, owner: TestUser, shared_workspace: dict[str, Any]
) -> None:
    response = await client.get(
        docs_url(shared_workspace, str(uuid.uuid4())), headers=owner.headers
    )
    assert response.status_code == 404


async def test_a_document_is_not_reachable_through_another_workspace(
    client: AsyncClient,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
) -> None:
    """Both workspaces belong to the caller, so what is under test here is the
    service's own workspace scoping, not the role dependency."""
    other = (
        await client.post("/workspaces", json={"name": "Other"}, headers=owner.headers)
    ).json()

    response = await client.get(docs_url(other, document["id"]), headers=owner.headers)
    assert response.status_code == 404


async def test_deleting_a_workspace_deletes_its_documents(
    client: AsyncClient,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    deleted = await client.delete(
        "/workspaces/%s" % shared_workspace["id"], headers=owner.headers
    )
    assert deleted.status_code == 204

    remaining = await document_service.find_document(
        db_session,
        uuid.UUID(shared_workspace["id"]),
        uuid.UUID(document["id"]),
    )
    assert remaining is None


# --------------------------------------------------------------------------- #
# Permission boundaries
# --------------------------------------------------------------------------- #


async def test_viewer_can_read(
    client: AsyncClient,
    viewer: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
) -> None:
    listed = await client.get(docs_url(shared_workspace), headers=viewer.headers)
    assert listed.status_code == 200

    single = await client.get(
        docs_url(shared_workspace, document["id"]), headers=viewer.headers
    )
    assert single.status_code == 200


async def test_viewer_cannot_patch(
    client: AsyncClient,
    viewer: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
) -> None:
    response = await client.patch(
        docs_url(shared_workspace, document["id"]),
        json={"version": 1, "title": "Renamed by a viewer"},
        headers=viewer.headers,
    )

    # 403, not 404: a viewer is a member and already knows the document exists.
    assert response.status_code == 403
    assert "editor" in response.json()["detail"]


async def test_refused_patch_writes_nothing(
    client: AsyncClient,
    owner: TestUser,
    viewer: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
) -> None:
    await client.patch(
        docs_url(shared_workspace, document["id"]),
        json={"version": 1, "title": "Should not stick", "content": PARAGRAPH},
        headers=viewer.headers,
    )

    current = (
        await client.get(
            docs_url(shared_workspace, document["id"]), headers=owner.headers
        )
    ).json()
    assert current["title"] == document["title"]
    assert current["content"] == document["content"]
    assert current["version"] == document["version"]


@pytest.mark.parametrize("method,addressed", [("post", False), ("delete", True)])
async def test_viewer_cannot_create_or_delete(
    client: AsyncClient,
    viewer: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
    method: str,
    addressed: bool,
) -> None:
    url = docs_url(shared_workspace, document["id"] if addressed else None)
    kwargs: dict[str, Any] = {"headers": viewer.headers}
    if method == "post":
        kwargs["json"] = {"title": "Forbidden"}

    response = await getattr(client, method)(url, **kwargs)
    assert response.status_code == 403


async def test_editor_can_write(
    client: AsyncClient, editor: TestUser, shared_workspace: dict[str, Any]
) -> None:
    created = await client.post(
        docs_url(shared_workspace),
        json={"title": "By the editor"},
        headers=editor.headers,
    )
    assert created.status_code == 201
    document_id = created.json()["id"]

    patched = await client.patch(
        docs_url(shared_workspace, document_id),
        json={"version": 1, "content": PARAGRAPH},
        headers=editor.headers,
    )
    assert patched.status_code == 200

    deleted = await client.delete(
        docs_url(shared_workspace, document_id), headers=editor.headers
    )
    assert deleted.status_code == 204


@pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
async def test_non_member_gets_404_everywhere(
    client: AsyncClient,
    outsider: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
    method: str,
) -> None:
    """404, never 403: neither the workspace's existence nor the document's may
    leak to a stranger."""
    url = (
        docs_url(shared_workspace)
        if method == "post"
        else docs_url(shared_workspace, document["id"])
    )
    kwargs: dict[str, Any] = {"headers": outsider.headers}
    if method == "post":
        kwargs["json"] = {"title": "Trespass"}
    elif method == "patch":
        kwargs["json"] = {"version": 1, "title": "Trespass"}

    response = await getattr(client, method)(url, **kwargs)
    assert response.status_code == 404
    assert response.json()["detail"] == "Workspace not found"


async def test_unauthenticated_requests_are_401(
    client: AsyncClient, shared_workspace: dict[str, Any]
) -> None:
    response = await client.get(docs_url(shared_workspace))
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Optimistic concurrency (the 409 path)
# --------------------------------------------------------------------------- #


async def test_stale_version_is_409_with_the_servers_state(
    client: AsyncClient,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
) -> None:
    await client.patch(
        docs_url(shared_workspace, document["id"]),
        json={"version": 1, "content": PARAGRAPH},
        headers=owner.headers,
    )

    stale = await client.patch(
        docs_url(shared_workspace, document["id"]),
        json={"version": 1, "title": "From a stale client"},
        headers=owner.headers,
    )

    assert stale.status_code == 409
    body = stale.json()
    assert "modified" in body["detail"]
    # Enough for the client to replace its editor state without another GET.
    assert body["current"]["version"] == 2
    assert body["current"]["content"] == PARAGRAPH
    assert body["current"]["id"] == document["id"]


async def test_stale_patch_does_not_modify_the_row(
    client: AsyncClient,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
) -> None:
    """The property that matters: a refused write is not a partial write."""
    winner = await client.patch(
        docs_url(shared_workspace, document["id"]),
        json={"version": 1, "title": "Winner", "content": PARAGRAPH},
        headers=owner.headers,
    )
    assert winner.status_code == 200

    stale = await client.patch(
        docs_url(shared_workspace, document["id"]),
        json={
            "version": 1,
            "title": "Loser",
            "content": {"type": "doc", "content": [{"type": "paragraph"}]},
        },
        headers=owner.headers,
    )
    assert stale.status_code == 409

    current = (
        await client.get(
            docs_url(shared_workspace, document["id"]), headers=owner.headers
        )
    ).json()
    assert current["title"] == "Winner"
    assert current["content"] == PARAGRAPH
    assert current["version"] == 2  # not 3: the refused write bumped nothing


async def test_retrying_with_the_returned_version_succeeds(
    client: AsyncClient,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
) -> None:
    await client.patch(
        docs_url(shared_workspace, document["id"]),
        json={"version": 1, "content": PARAGRAPH},
        headers=owner.headers,
    )
    conflict = await client.patch(
        docs_url(shared_workspace, document["id"]),
        json={"version": 1, "title": "Retry me"},
        headers=owner.headers,
    )

    retry = await client.patch(
        docs_url(shared_workspace, document["id"]),
        json={"version": conflict.json()["current"]["version"], "title": "Retry me"},
        headers=owner.headers,
    )
    assert retry.status_code == 200
    assert retry.json()["version"] == 3


async def test_a_version_ahead_of_the_server_is_also_409(
    client: AsyncClient,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
) -> None:
    response = await client.patch(
        docs_url(shared_workspace, document["id"]),
        json={"version": 99, "title": "From the future"},
        headers=owner.headers,
    )
    assert response.status_code == 409
    assert response.json()["current"]["version"] == 1


async def test_concurrent_patches_cannot_both_be_applied() -> None:
    """Two connections PATCHing the same version at the same moment.

    This test owns its data rather than using the shared fixtures: those live in
    an uncommitted transaction a second connection cannot see, and the race is
    only real across connections. Rows are committed for real, hence the
    unconditional cleanup.
    """
    engine = create_async_engine(_TEST_DATABASE_URL, poolclass=NullPool)
    email = "race-%s@example.com" % uuid.uuid4().hex[:12]

    try:
        async with AsyncSession(engine, expire_on_commit=False) as setup:
            user = User(email=email, hashed_password="not-a-real-hash", name="Racer")
            setup.add(user)
            await setup.flush()
            workspace = Workspace(name="Race", owner_id=user.id)
            setup.add(workspace)
            await setup.commit()
            created = await document_service.create_document(
                setup,
                workspace_id=workspace.id,
                created_by=user.id,
                title="Contended",
            )
            workspace_id, document_id = workspace.id, created.id

        async def attempt(title: str) -> tuple[str, int]:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                try:
                    updated = await document_service.update_document(
                        session,
                        workspace_id=workspace_id,
                        document_id=document_id,
                        expected_version=1,
                        title=title,
                    )
                    return "applied", updated.version
                except document_service.StaleDocumentVersionError as exc:
                    return "stale", exc.current.version

        outcomes = await asyncio.gather(attempt("Writer A"), attempt("Writer B"))

        assert sorted(outcome for outcome, _ in outcomes) == ["applied", "stale"]
        # The loser is told version 2, not 3: its own write never happened.
        assert {version for _, version in outcomes} == {2}

        async with AsyncSession(engine) as check:
            final = await document_service.get_document(
                check, workspace_id, document_id
            )
            assert final.version == 2
            assert final.title in {"Writer A", "Writer B"}
    finally:
        async with AsyncSession(engine) as cleanup:
            await cleanup.execute(delete(User).where(User.email == email))
            await cleanup.commit()
        await engine.dispose()


# --------------------------------------------------------------------------- #
# Request validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "body",
    [
        {"version": 1},  # nothing to change
        {"title": "No version"},  # version is mandatory
        {"version": 0, "title": "Bad version"},
        {"version": 1, "title": "   "},  # blank once stripped
        {"version": 1, "content": {"type": "paragraph"}},  # not a doc node
        {"version": 1, "content": []},  # not an object at all
    ],
)
async def test_invalid_patch_bodies_are_rejected(
    client: AsyncClient,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    document: dict[str, Any],
    body: dict[str, Any],
) -> None:
    response = await client.patch(
        docs_url(shared_workspace, document["id"]),
        json=body,
        headers=owner.headers,
    )
    assert response.status_code == 422, response.text


async def test_create_requires_a_title(
    client: AsyncClient, owner: TestUser, shared_workspace: dict[str, Any]
) -> None:
    response = await client.post(
        docs_url(shared_workspace), json={"title": "  "}, headers=owner.headers
    )
    assert response.status_code == 422
