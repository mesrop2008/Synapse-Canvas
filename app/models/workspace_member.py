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
    """Join row between a user and a workspace, carrying the user's role.

    The owner of a workspace also gets a row here (role=owner) rather than
    being implied by `Workspace.owner_id` alone. One membership table means
    the permission check is a single lookup with no special case for owners.
    """

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
        # `values_callable` makes SQLAlchemy persist the enum *values*
        # ("owner"), not the member names ("OWNER"), which is what the API
        # exposes and what anyone reading the table by hand would expect.
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
