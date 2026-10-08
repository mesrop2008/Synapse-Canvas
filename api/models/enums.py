from __future__ import annotations

from enum import StrEnum


class WorkspaceRole(StrEnum):
    """Ordered owner > editor > viewer, for "at least editor" checks."""

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


class AIQueryMode(StrEnum):
    CONTINUE = "continue"
    REWRITE = "rewrite"
    SUMMARIZE = "summarize"
    ASK = "ask"


class AIQueryStatus(StrEnum):
    STREAMING = "streaming"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


AI_QUERY_MODE_ENUM_NAME = "ai_query_mode"
AI_QUERY_STATUS_ENUM_NAME = "ai_query_status"
