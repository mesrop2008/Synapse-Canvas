"""SQLAlchemy models. Import every model here so it is registered on
Base.metadata before Alembic inspects it -- a missed one vanishes from
migrations."""

from app.db.base import Base
from app.models.document import Document
from app.models.email_verification import EmailVerificationToken
from app.models.enums import WORKSPACE_ROLE_ENUM_NAME, WorkspaceRole
from app.models.rate_limit import RateLimitBucket
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember

__all__ = [
    "Base",
    "Document",
    "EmailVerificationToken",
    "RateLimitBucket",
    "RefreshToken",
    "User",
    "Workspace",
    "WorkspaceMember",
    "WorkspaceRole",
    "WORKSPACE_ROLE_ENUM_NAME",
]
