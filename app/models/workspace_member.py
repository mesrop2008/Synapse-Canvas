from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import WORKSPACE_ROLE_ENUM_NAME, WorkspaceRole

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace import Workspace


class WorkspaceMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """User-workspace join row carrying the role. The owner gets a row too
    (role=owner), so the permission check is one lookup with no special case."""

    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "workspace_id",
            name="uq_workspace_members_user_id_workspace_id",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        # values_callable persists the values ("owner"), not the names ("OWNER").
        SAEnum(
            WorkspaceRole,
            name=WORKSPACE_ROLE_ENUM_NAME,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="memberships")
    workspace: Mapped["Workspace"] = relationship(back_populates="members")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<WorkspaceMember user_id={self.user_id} "
            f"workspace_id={self.workspace_id} role={self.role}>"
        )
