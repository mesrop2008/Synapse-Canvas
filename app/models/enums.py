"""Enumerations shared by the ORM and the API schemas."""

from __future__ import annotations

from enum import StrEnum


class WorkspaceRole(StrEnum):
    """A member's capability level, totally ordered owner > editor > viewer so
    the permission dependency can require "at least editor". StrEnum keeps the
    lowercase values as the wire/DB/f-string form."""

    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"

    @property
    def rank(self) -> int:
        return _ROLE_RANK[self]

    def satisfies(self, minimum: "WorkspaceRole") -> bool:
        return self.rank >= minimum.rank


_ROLE_RANK: dict[WorkspaceRole, int] = {
    WorkspaceRole.VIEWER: 1,
    WorkspaceRole.EDITOR: 2,
    WorkspaceRole.OWNER: 3,
}

WORKSPACE_ROLE_ENUM_NAME = "workspace_role"
