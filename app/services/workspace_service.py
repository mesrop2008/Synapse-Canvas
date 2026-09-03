"""Workspace and membership business logic."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.enums import WorkspaceRole
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember
from app.services import auth_service


async def create_workspace(
    db: AsyncSession, owner: User, name: str
) -> tuple[Workspace, WorkspaceRole]:
    """Create a workspace and its owner membership as one unit of work.

    Both rows are added before a single commit, so there is no window in which
    a workspace exists with nobody able to administer it.
    """
    workspace = Workspace(name=name, owner_id=owner.id)
    db.add(workspace)
    # A mapped_column default is evaluated during flush, not at construction,
    # so workspace.id is still None here. Flush to populate it before the
    # membership row references it. This stays inside the same transaction --
    # the single commit below still covers both rows.
    await db.flush()

    db.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=owner.id,
            role=WorkspaceRole.OWNER,
        )
    )
    await db.commit()
    await db.refresh(workspace)
    return workspace, WorkspaceRole.OWNER


async def get_workspace_with_role(
    db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[Workspace, WorkspaceRole] | None:
    """Fetch a workspace together with `user_id`'s role in it.

    The inner join is the whole point: a workspace that does not exist and a
    workspace the caller is not a member of both produce no row, so callers
    physically cannot distinguish the two and leak existence by accident.
    """
    result = await db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(Workspace.id == workspace_id, WorkspaceMember.user_id == user_id)
    )
    row = result.first()
    return (row[0], row[1]) if row is not None else None


async def list_workspaces_for_user(
    db: AsyncSession, user_id: uuid.UUID
) -> list[tuple[Workspace, WorkspaceRole]]:
    result = await db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user_id)
        .order_by(Workspace.created_at.desc())
    )
    return [(row[0], row[1]) for row in result.all()]


async def update_workspace(
    db: AsyncSession, workspace: Workspace, *, name: str | None = None
) -> Workspace:
    if name is not None:
        workspace.name = name
    await db.commit()
    await db.refresh(workspace)
    return workspace


async def delete_workspace(db: AsyncSession, workspace: Workspace) -> None:
    # Membership rows go with it via ON DELETE CASCADE at the database level.
    await db.delete(workspace)
    await db.commit()


# --- Membership ------------------------------------------------------------


async def list_members(
    db: AsyncSession, workspace_id: uuid.UUID
) -> list[WorkspaceMember]:
    result = await db.execute(
        select(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .options(selectinload(WorkspaceMember.user))
        .order_by(WorkspaceMember.created_at)
    )
    return list(result.scalars().all())


async def add_member(
    db: AsyncSession, workspace: Workspace, email: str, role: WorkspaceRole
) -> WorkspaceMember:
    user = await auth_service.get_user_by_email(db, email)
    if user is None:
        # Distinct from "workspace not found": the caller is a proven owner of
        # this workspace, so there is no existence to protect here.
        raise NotFoundError("No user with that email address")

    if not user.is_email_verified:
        # Membership is granted by email address, so admitting an account that
        # never proved control of its address would let a squatter inherit an
        # invitation meant for the address's real owner. Defence in depth:
        # login already refuses unverified accounts.
        raise ConflictError(
            "That user has not verified their email address yet"
        )

    existing = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("User is already a member of this workspace")

    member = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=user.id,
        role=role,
    )
    # Populate the relationship from the object already in the identity map.
    # The response serialises `member.user`, and a lazy load after commit
    # would raise MissingGreenlet under async SQLAlchemy.
    member.user = user
    db.add(member)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("User is already a member of this workspace") from exc

    await db.refresh(member, attribute_names=["id", "created_at", "role"])
    return member


async def remove_member(
    db: AsyncSession, workspace: Workspace, user_id: uuid.UUID
) -> None:
    if user_id == workspace.owner_id:
        # A workspace with no owner would be unadministrable, and every
        # owner-only route would 403 forever. Transferring ownership is a
        # separate operation, out of scope for Part 1.
        raise ConflictError(
            "The workspace owner cannot be removed. Transfer ownership first."
        )

    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise NotFoundError("User is not a member of this workspace")

    await db.delete(member)
    await db.commit()
