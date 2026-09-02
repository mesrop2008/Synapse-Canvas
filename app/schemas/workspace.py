from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models.enums import WorkspaceRole
from app.schemas.common import NonEmptyName
from app.schemas.user import UserRead


class WorkspaceCreate(BaseModel):
    name: NonEmptyName


class WorkspaceUpdate(BaseModel):
    """PATCH body. Every field optional; only fields actually sent are applied.

    Handlers use `model_dump(exclude_unset=True)` so that an explicit `null`
    and an omitted key stay distinguishable.
    """

    name: NonEmptyName | None = None


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    owner_id: uuid.UUID
    created_at: datetime


class WorkspaceWithRole(WorkspaceRead):
    """A workspace plus the *calling* user's role in it.

    Returned from the endpoints that already resolved the caller's role, so a
    client can render permissions without a second round trip per workspace.
    """

    role: WorkspaceRole


class MemberAdd(BaseModel):
    email: EmailStr
    role: WorkspaceRole


class MemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    role: WorkspaceRole
    created_at: datetime
    user: UserRead


def workspace_with_role(workspace: object, role: WorkspaceRole) -> WorkspaceWithRole:
    """Build a `WorkspaceWithRole` from an ORM workspace plus a resolved role."""
    return WorkspaceWithRole(
        **WorkspaceRead.model_validate(workspace).model_dump(),
        role=role,
    )
