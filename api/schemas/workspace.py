from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from api.models.enums import WorkspaceRole
from api.schemas.common import NonEmptyName
from api.schemas.user import UserRead


class WorkspaceCreate(BaseModel):
    name: NonEmptyName


class WorkspaceUpdate(BaseModel):
    """Dumped with exclude_unset, so `null` and an omitted key differ."""

    name: NonEmptyName | None = None


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    owner_id: uuid.UUID
    created_at: datetime


class WorkspaceWithRole(WorkspaceRead):

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
    return WorkspaceWithRole(
        **WorkspaceRead.model_validate(workspace).model_dump(),
        role=role,
    )
