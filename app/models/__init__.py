"""SQLAlchemy models.

Importing every model here guarantees they are registered on `Base.metadata`
before Alembic autogenerate or `create_all` inspects it. Miss one and it
silently vanishes from migrations.
"""

from app.db.base import Base
from app.models.enums import WORKSPACE_ROLE_ENUM_NAME, WorkspaceRole
from app.models.rate_limit import RateLimitBucket
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember

__all__ = [
    "Base",
    "RateLimitBucket",
    "RefreshToken",
    "User",
    "Workspace",
    "WorkspaceMember",
    "WorkspaceRole",
    "WORKSPACE_ROLE_ENUM_NAME",
]
