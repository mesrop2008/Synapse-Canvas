"""Permission boundaries.

These are the tests that matter most: they pin down who is refused, and with
which status code. The 404-versus-403 split is a security property, not a
cosmetic one, so it is asserted explicitly rather than inferred from "not 200".
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import TestUser


async def test_viewer_cannot_rename_a_workspace(
    client: AsyncClient, viewer: TestUser, shared_workspace: dict[str, Any]
) -> None:
    response = await client.patch(
        "/workspaces/%s" % shared_workspace["id"],
        json={"name": "Renamed by a viewer"},
        headers=viewer.headers,
    )

    # 403, not 404: the viewer is a member and already knows this workspace
    # exists, so hiding it would be misleading rather than protective.
    assert response.status_code == 403
    assert "owner" in response.json()["detail"]


async def test_editor_cannot_rename_a_workspace(
    client: AsyncClient, editor: TestUser, shared_workspace: dict[str, Any]
) -> None:
    response = await client.patch(
        "/workspaces/%s" % shared_workspace["id"],
        json={"name": "Renamed by an editor"},
        headers=editor.headers,
    )
    assert response.status_code == 403


async def test_rejected_rename_does_not_change_the_name(
    client: AsyncClient,
    owner: TestUser,
    viewer: TestUser,
    shared_workspace: dict[str, Any],
) -> None:
    """A 403 must also mean nothing was written."""
    await client.patch(
        "/workspaces/%s" % shared_workspace["id"],
        json={"name": "Should not stick"},
        headers=viewer.headers,
    )

    current = await client.get(
        "/workspaces/%s" % shared_workspace["id"], headers=owner.headers
    )
    assert current.json()["name"] == shared_workspace["name"]


@pytest.mark.parametrize("role", ["editor", "viewer"])
async def test_non_owners_cannot_delete_a_workspace(
    client: AsyncClient,
    editor: TestUser,
    viewer: TestUser,
    shared_workspace: dict[str, Any],
    role: str,
) -> None:
    actor = {"editor": editor, "viewer": viewer}[role]

    response = await client.delete(
        "/workspaces/%s" % shared_workspace["id"], headers=actor.headers
    )
    assert response.status_code == 403


@pytest.mark.parametrize("role", ["editor", "viewer"])
async def test_non_owners_cannot_add_members(
    client: AsyncClient,
    editor: TestUser,
    viewer: TestUser,
    outsider: TestUser,
    shared_workspace: dict[str, Any],
    role: str,
) -> None:
    actor = {"editor": editor, "viewer": viewer}[role]

    response = await client.post(
        "/workspaces/%s/members" % shared_workspace["id"],
        json={"email": outsider.email, "role": "viewer"},
        headers=actor.headers,
    )
    assert response.status_code == 403


async def test_members_of_any_role_can_read_the_workspace(
    client: AsyncClient,
    owner: TestUser,
    editor: TestUser,
    viewer: TestUser,
    shared_workspace: dict[str, Any],
) -> None:
    for user, expected_role in ((owner, "owner"), (editor, "editor"), (viewer, "viewer")):
        response = await client.get(
            "/workspaces/%s" % shared_workspace["id"], headers=user.headers
        )
        assert response.status_code == 200, user.name
        assert response.json()["role"] == expected_role


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("GET", "", None),
        ("PATCH", "", {"name": "Hijacked"}),
        ("DELETE", "", None),
        ("GET", "/members", None),
        ("POST", "/members", {"email": "someone@example.com", "role": "viewer"}),
    ],
)
async def test_non_member_gets_404_on_every_workspace_route(
    client: AsyncClient,
    outsider: TestUser,
    shared_workspace: dict[str, Any],
    method: str,
    suffix: str,
    body: dict[str, Any] | None,
) -> None:
    """Existence must not leak through any route.

    A 403 anywhere here would tell an outsider that this workspace id is real,
    turning the id space into something worth probing.
    """
    url = "/workspaces/%s%s" % (shared_workspace["id"], suffix)

    response = await client.request(
        method, url, json=body, headers=outsider.headers
    )

    assert response.status_code == 404, "%s %s leaked existence" % (method, suffix)
    assert response.json()["detail"] == "Workspace not found"


async def test_non_member_sees_the_same_404_as_for_a_nonexistent_workspace(
    client: AsyncClient, outsider: TestUser, shared_workspace: dict[str, Any]
) -> None:
    """The two responses must be byte-identical, or the difference is the leak."""
    real_but_forbidden = await client.get(
        "/workspaces/%s" % shared_workspace["id"], headers=outsider.headers
    )
    pure_fiction = await client.get(
        "/workspaces/%s" % uuid.uuid4(), headers=outsider.headers
    )

    assert real_but_forbidden.status_code == pure_fiction.status_code == 404
    assert real_but_forbidden.json() == pure_fiction.json()


async def test_removed_member_immediately_loses_access(
    client: AsyncClient,
    owner: TestUser,
    editor: TestUser,
    shared_workspace: dict[str, Any],
) -> None:
    workspace_id = shared_workspace["id"]
    assert (
        await client.get("/workspaces/%s" % workspace_id, headers=editor.headers)
    ).status_code == 200

    removed = await client.delete(
        "/workspaces/%s/members/%s" % (workspace_id, editor.id),
        headers=owner.headers,
    )
    assert removed.status_code == 204

    # Their access token is still perfectly valid; membership is what changed.
    after = await client.get(
        "/workspaces/%s" % workspace_id, headers=editor.headers
    )
    assert after.status_code == 404


async def test_authentication_is_checked_before_membership(
    client: AsyncClient, shared_workspace: dict[str, Any]
) -> None:
    """No token means 401, not the 404 an authenticated stranger would see."""
    response = await client.get("/workspaces/%s" % shared_workspace["id"])
    assert response.status_code == 401


async def test_owner_cannot_be_removed_from_their_own_workspace(
    client: AsyncClient, owner: TestUser, shared_workspace: dict[str, Any]
) -> None:
    """Removing the owner would leave the workspace unadministrable forever."""
    response = await client.delete(
        "/workspaces/%s/members/%s" % (shared_workspace["id"], owner.id),
        headers=owner.headers,
    )

    assert response.status_code == 409
    assert "owner cannot be removed" in response.json()["detail"]

    members = await client.get(
        "/workspaces/%s/members" % shared_workspace["id"], headers=owner.headers
    )
    owner_rows = [m for m in members.json() if m["user_id"] == str(owner.id)]
    assert len(owner_rows) == 1
    assert owner_rows[0]["role"] == "owner"


async def test_owner_still_governs_the_workspace_after_the_refused_removal(
    client: AsyncClient, owner: TestUser, shared_workspace: dict[str, Any]
) -> None:
    await client.delete(
        "/workspaces/%s/members/%s" % (shared_workspace["id"], owner.id),
        headers=owner.headers,
    )

    still_owner = await client.patch(
        "/workspaces/%s" % shared_workspace["id"],
        json={"name": "Still in charge"},
        headers=owner.headers,
    )
    assert still_owner.status_code == 200


async def test_owner_can_add_a_member_by_email(
    client: AsyncClient, owner: TestUser, outsider: TestUser, workspace: dict[str, Any]
) -> None:
    response = await client.post(
        "/workspaces/%s/members" % workspace["id"],
        json={"email": outsider.email, "role": "editor"},
        headers=owner.headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user_id"] == str(outsider.id)
    assert body["role"] == "editor"
    assert body["user"]["email"] == outsider.email


async def test_adding_a_member_is_case_insensitive_on_email(
    client: AsyncClient, owner: TestUser, outsider: TestUser, workspace: dict[str, Any]
) -> None:
    response = await client.post(
        "/workspaces/%s/members" % workspace["id"],
        json={"email": outsider.email.upper(), "role": "viewer"},
        headers=owner.headers,
    )
    assert response.status_code == 201


async def test_adding_an_unregistered_email_returns_404(
    client: AsyncClient, owner: TestUser, workspace: dict[str, Any]
) -> None:
    response = await client.post(
        "/workspaces/%s/members" % workspace["id"],
        json={"email": "ghost@example.com", "role": "viewer"},
        headers=owner.headers,
    )

    assert response.status_code == 404
    # Distinguishable from the workspace 404: the caller is a proven owner
    # here, so there is no workspace existence left to protect.
    assert response.json()["detail"] == "No user with that email address"


async def test_adding_the_same_member_twice_returns_409(
    client: AsyncClient, owner: TestUser, outsider: TestUser, workspace: dict[str, Any]
) -> None:
    payload = {"email": outsider.email, "role": "viewer"}
    url = "/workspaces/%s/members" % workspace["id"]

    assert (
        await client.post(url, json=payload, headers=owner.headers)
    ).status_code == 201

    duplicate = await client.post(url, json=payload, headers=owner.headers)
    assert duplicate.status_code == 409


async def test_adding_a_member_rejects_an_unknown_role(
    client: AsyncClient, owner: TestUser, outsider: TestUser, workspace: dict[str, Any]
) -> None:
    response = await client.post(
        "/workspaces/%s/members" % workspace["id"],
        json={"email": outsider.email, "role": "administrator"},
        headers=owner.headers,
    )
    assert response.status_code == 422


async def test_owner_can_remove_an_ordinary_member(
    client: AsyncClient,
    owner: TestUser,
    viewer: TestUser,
    shared_workspace: dict[str, Any],
) -> None:
    response = await client.delete(
        "/workspaces/%s/members/%s" % (shared_workspace["id"], viewer.id),
        headers=owner.headers,
    )
    assert response.status_code == 204

    members = await client.get(
        "/workspaces/%s/members" % shared_workspace["id"], headers=owner.headers
    )
    assert str(viewer.id) not in {m["user_id"] for m in members.json()}


async def test_removing_someone_who_is_not_a_member_returns_404(
    client: AsyncClient,
    owner: TestUser,
    outsider: TestUser,
    shared_workspace: dict[str, Any],
) -> None:
    response = await client.delete(
        "/workspaces/%s/members/%s" % (shared_workspace["id"], outsider.id),
        headers=owner.headers,
    )
    assert response.status_code == 404


@pytest.mark.parametrize("role", ["editor", "viewer"])
async def test_non_owners_cannot_remove_members(
    client: AsyncClient,
    editor: TestUser,
    viewer: TestUser,
    shared_workspace: dict[str, Any],
    role: str,
) -> None:
    actor = {"editor": editor, "viewer": viewer}[role]
    target = viewer if role == "editor" else editor

    response = await client.delete(
        "/workspaces/%s/members/%s" % (shared_workspace["id"], target.id),
        headers=actor.headers,
    )
    assert response.status_code == 403
