"""Workspace and membership endpoints. Every `/{workspace_id}` route gets its
workspace from a WorkspaceAccess dependency, so no handler re-fetches it or
re-checks permissions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.dependencies import CurrentUser, DbSession, RequireOwner, RequireViewer
from app.schemas.workspace import (
    MemberAdd,
    MemberRead,
    WorkspaceCreate,
    WorkspaceUpdate,
    WorkspaceWithRole,
    workspace_with_role,
)
from app.services import workspace_service

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

# The access dependency collapses "no such workspace" and "not a member" into
# one 404.
_MEMBERSHIP_RESPONSES = {
    401: {"description": "Missing or invalid access token"},
    404: {"description": "Workspace not found, or caller is not a member"},
}
_OWNER_RESPONSES = {
    **_MEMBERSHIP_RESPONSES,
    403: {"description": "Caller is a member but not the owner"},
}


@router.post(
    "",
    response_model=WorkspaceWithRole,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workspace (caller becomes its owner)",
)
async def create_workspace(
    payload: WorkspaceCreate, current_user: CurrentUser, db: DbSession
) -> WorkspaceWithRole:
    workspace, role = await workspace_service.create_workspace(
        db, current_user, payload.name
    )
    return workspace_with_role(workspace, role)


@router.get(
    "",
    response_model=list[WorkspaceWithRole],
    summary="List workspaces the caller belongs to",
)
async def list_workspaces(
    current_user: CurrentUser, db: DbSession
) -> list[WorkspaceWithRole]:
    rows = await workspace_service.list_workspaces_for_user(db, current_user.id)
    return [workspace_with_role(ws, role) for ws, role in rows]


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceWithRole,
    summary="Fetch a single workspace",
    responses=_MEMBERSHIP_RESPONSES,
)
async def get_workspace(ctx: RequireViewer) -> WorkspaceWithRole:
    return workspace_with_role(ctx.workspace, ctx.role)


@router.patch(
    "/{workspace_id}",
    response_model=WorkspaceWithRole,
    summary="Update a workspace (owner only)",
    responses=_OWNER_RESPONSES,
)
async def update_workspace(
    payload: WorkspaceUpdate, ctx: RequireOwner, db: DbSession
) -> WorkspaceWithRole:
    updates = payload.model_dump(exclude_unset=True)
    workspace = await workspace_service.update_workspace(db, ctx.workspace, **updates)
    return workspace_with_role(workspace, ctx.role)


@router.delete(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a workspace (owner only)",
    responses=_OWNER_RESPONSES,
)
async def delete_workspace(ctx: RequireOwner, db: DbSession) -> None:
    await workspace_service.delete_workspace(db, ctx.workspace)


@router.get(
    "/{workspace_id}/members",
    response_model=list[MemberRead],
    summary="List workspace members",
    responses=_MEMBERSHIP_RESPONSES,
)
async def list_members(ctx: RequireViewer, db: DbSession) -> list[MemberRead]:
    members = await workspace_service.list_members(db, ctx.workspace.id)
    return [MemberRead.model_validate(m) for m in members]


@router.post(
    "/{workspace_id}/members",
    response_model=MemberRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a member by email (owner only)",
    responses={
        **_OWNER_RESPONSES,
        409: {"description": "User is already a member"},
    },
)
async def add_member(
    payload: MemberAdd, ctx: RequireOwner, db: DbSession
) -> MemberRead:
    member = await workspace_service.add_member(
        db, ctx.workspace, payload.email, payload.role
    )
    return MemberRead.model_validate(member)


@router.delete(
    "/{workspace_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member (owner only; the owner cannot be removed)",
    responses={
        **_OWNER_RESPONSES,
        409: {"description": "Refused: that user owns the workspace"},
    },
)
async def remove_member(user_id: uuid.UUID, ctx: RequireOwner, db: DbSession) -> None:
    await workspace_service.remove_member(db, ctx.workspace, user_id)
