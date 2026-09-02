"""Enumerations shared by the ORM and the API schemas."""

from __future__ import annotations

from enum import StrEnum


class WorkspaceRole(StrEnum):
    """A member's capability level within a workspace.

    Roles are totally ordered: owner > editor > viewer. Encoding that order
    once, here, is what lets the permission dependency express requirements as
    "at least editor" instead of enumerating every acceptable role at each
    route.

    `StrEnum` (3.11+) guarantees `str(role) == role.value`, so the lowercase
    wire values are what land in JSON, in the database, and in f-strings.
    """

    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"

    @property
    def rank(self) -> int:
        return _ROLE_RANK[self]

    def satisfies(self, minimum: "WorkspaceRole") -> bool:
        """True if this role is at least as privileged as `minimum`."""
        return self.rank >= minimum.rank


_ROLE_RANK: dict[WorkspaceRole, int] = {
    WorkspaceRole.VIEWER: 1,
    WorkspaceRole.EDITOR: 2,
    WorkspaceRole.OWNER: 3,
}

# Name of the PostgreSQL enum type backing the column. Referenced by the
# migration, so it lives next to the enum itself.
WORKSPACE_ROLE_ENUM_NAME = "workspace_role"
