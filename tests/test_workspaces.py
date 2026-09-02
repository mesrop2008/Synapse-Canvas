"""Workspace CRUD, membership bookkeeping and listing scope."""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WorkspaceMember
from tests.conftest import TestUser, UserFactory


# --- Creation ---------------------------------------------------------------


async def test_create_workspace_makes_the_caller_owner(
    client: AsyncClient, owner: TestUser
) -> None:
    response = await client.post(
        "/workspaces", json={"name": "Protein Folding"}, headers=owner.headers
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Protein Folding"
    assert body["owner_id"] == str(owner.id)
    assert body["role"] == "owner"


async def test_create_workspace_also_creates_the_owner_membership(
    client: AsyncClient, owner: TestUser, db_session: AsyncSession
) -> None:
    """The owner must exist in workspace_members, not just via owner_id.

    Checked in the database rather than through the API, because it is the
    membership row that every permission decision depends on.
    """
    response = await client.post(
        "/workspaces", json={"name": "Ribosomes"}, headers=owner.headers
    )
    workspace_id = uuid.UUID(response.json()["id"])

    result = await db_session.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)
    )
    members = result.scalars().all()

    assert len(members) == 1
    assert members[0].user_id == owner.id
    assert members[0].role == "owner"


async def test_create_workspace_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/workspaces", json={"name": "Anonymous"})
    assert response.status_code == 401


async def test_create_workspace_rejects_a_blank_name(
    client: AsyncClient, owner: TestUser
) -> None:
    response = await client.post(
        "/workspaces", json={"name": "   "}, headers=owner.headers
    )
    assert response.status_code == 422


# --- Listing ----------------------------------------------------------------


async def test_list_returns_only_workspaces_the_caller_belongs_to(
    client: AsyncClient, owner: TestUser, outsider: TestUser
) -> None:
    await client.post("/workspaces", json={"name": "Mine A"}, headers=owner.headers)
    await client.post("/workspaces", json={"name": "Mine B"}, headers=owner.headers)
    await client.post(
        "/workspaces", json={"name": "Theirs"}, headers=outsider.headers
    )

    mine = await client.get("/workspaces", headers=owner.headers)
    theirs = await client.get("/workspaces", headers=outsider.headers)

    assert {w["name"] for w in mine.json()} == {"Mine A", "Mine B"}
    assert {w["name"] for w in theirs.json()} == {"Theirs"}


async def test_list_includes_workspaces_joined_as_a_member(
    client: AsyncClient,
    owner: TestUser,
    editor: TestUser,
    shared_workspace: dict[str, Any],
) -> None:
    response = await client.get("/workspaces", headers=editor.headers)

    assert response.status_code == 200
    listed = response.json()
    assert len(listed) == 1
    assert listed[0]["id"] == shared_workspace["id"]
    # The role reported is the caller's, not the workspace owner's.
    assert listed[0]["role"] == "editor"
    assert listed[0]["owner_id"] == str(owner.id)


async def test_list_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/workspaces")).status_code == 401


# --- Retrieval --------------------------------------------------------------


async def test_get_workspace_returns_it_with_the_callers_role(
    client: AsyncClient, viewer: TestUser, shared_workspace: dict[str, Any]
) -> None:
    response = await client.get(
        "/workspaces/%s" % shared_workspace["id"], headers=viewer.headers
    )

    assert response.status_code == 200
    assert response.json()["id"] == shared_workspace["id"]
    assert response.json()["role"] == "viewer"


async def test_get_unknown_workspace_returns_404(
    client: AsyncClient, owner: TestUser
) -> None:
    response = await client.get(
        "/workspaces/%s" % uuid.uuid4(), headers=owner.headers
    )
    assert response.status_code == 404


async def test_get_malformed_workspace_id_returns_422(
    client: AsyncClient, owner: TestUser
) -> None:
    response = await client.get("/workspaces/not-a-uuid", headers=owner.headers)
    assert response.status_code == 422


# --- Update -----------------------------------------------------------------


async def test_owner_can_rename_a_workspace(
    client: AsyncClient, owner: TestUser, workspace: dict[str, Any]
) -> None:
    response = await client.patch(
        "/workspaces/%s" % workspace["id"],
        json={"name": "Renamed"},
        headers=owner.headers,
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"

    refetched = await client.get(
        "/workspaces/%s" % workspace["id"], headers=owner.headers
    )
    assert refetched.json()["name"] == "Renamed"


async def test_patch_with_no_fields_leaves_the_workspace_untouched(
    client: AsyncClient, owner: TestUser, workspace: dict[str, Any]
) -> None:
    """exclude_unset means an omitted key is not the same as an explicit null."""
    response = await client.patch(
        "/workspaces/%s" % workspace["id"], json={}, headers=owner.headers
    )

    assert response.status_code == 200
    assert response.json()["name"] == workspace["name"]


async def test_rename_rejects_a_blank_name(
    client: AsyncClient, owner: TestUser, workspace: dict[str, Any]
) -> None:
    response = await client.patch(
        "/workspaces/%s" % workspace["id"],
        json={"name": "   "},
        headers=owner.headers,
    )
    assert response.status_code == 422


# --- Deletion ---------------------------------------------------------------


async def test_owner_can_delete_a_workspace(
    client: AsyncClient, owner: TestUser, workspace: dict[str, Any]
) -> None:
    deleted = await client.delete(
        "/workspaces/%s" % workspace["id"], headers=owner.headers
    )
    assert deleted.status_code == 204

    gone = await client.get(
        "/workspaces/%s" % workspace["id"], headers=owner.headers
    )
    assert gone.status_code == 404


async def test_deleting_a_workspace_cascades_to_its_memberships(
    client: AsyncClient,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    """ON DELETE CASCADE, verified rather than assumed."""
    workspace_id = uuid.UUID(shared_workspace["id"])

    before = await db_session.scalar(
        select(func.count())
        .select_from(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == workspace_id)
    )
    assert before == 3  # owner + editor + viewer

    await client.delete("/workspaces/%s" % workspace_id, headers=owner.headers)

    after = await db_session.scalar(
        select(func.count())
        .select_from(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == workspace_id)
    )
    assert after == 0


async def test_workspaces_are_isolated_between_users(
    client: AsyncClient, make_user: UserFactory
) -> None:
    alice = await make_user(name="Alice")
    bob = await make_user(name="Bob")

    created = await client.post(
        "/workspaces", json={"name": "Alice only"}, headers=alice.headers
    )
    workspace_id = created.json()["id"]

    assert (
        await client.get("/workspaces/%s" % workspace_id, headers=bob.headers)
    ).status_code == 404
    assert (
        await client.get("/workspaces/%s" % workspace_id, headers=alice.headers)
    ).status_code == 200
