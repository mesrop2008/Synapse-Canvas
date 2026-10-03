from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.core.exceptions import ConflictError, ErrorCode, NotFoundError
from api.models.enums import WorkspaceRole
from api.models.user import User
from api.models.workspace import Workspace
from api.models.workspace_member import WorkspaceMember
from api.services import auth_service


async def create_workspace(
    db: AsyncSession, owner: User, name: str
) -> tuple[Workspace, WorkspaceRole]:
    workspace = Workspace(name=name, owner_id=owner.id)
    db.add(workspace)
    # Flush for the id, which the membership row needs.
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
    """No such workspace and not a member both return None, indistinguishably."""
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
    await db.delete(workspace)
    await db.commit()


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
        raise NotFoundError(
            "No user with that email address", code=ErrorCode.MEMBER_UNKNOWN_EMAIL
        )

    if not user.is_email_verified:
        # Membership is by address: an unverified account could be a squatter.
        raise ConflictError(
            "That user has not verified their email address yet",
            code=ErrorCode.MEMBER_UNVERIFIED,
        )

    existing = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(
            "User is already a member of this workspace",
            code=ErrorCode.MEMBER_DUPLICATE,
        )

    member = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=user.id,
        role=role,
    )
    # Eager: a lazy load of member.user after commit raises MissingGreenlet.
    member.user = user
    db.add(member)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            "User is already a member of this workspace",
            code=ErrorCode.MEMBER_DUPLICATE,
        ) from exc

    await db.refresh(member, attribute_names=["id", "created_at", "role"])
    return member


async def remove_member(
    db: AsyncSession, workspace: Workspace, user_id: uuid.UUID
) -> None:
    if user_id == workspace.owner_id:
        raise ConflictError(
            "The workspace owner cannot be removed. Transfer ownership first.",
            code=ErrorCode.MEMBER_IS_OWNER,
        )

    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise NotFoundError(
            "User is not a member of this workspace", code=ErrorCode.MEMBER_ABSENT
        )

    await db.delete(member)
    await db.commit()
