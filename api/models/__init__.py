"""SQLAlchemy models. Import every model here so it is registered on
Base.metadata before Alembic inspects it -- a missed one vanishes from
migrations."""

from api.db.base import Base
from api.models.document import Document
from api.models.email_verification import EmailVerificationToken
from api.models.enums import WORKSPACE_ROLE_ENUM_NAME, WorkspaceRole
from api.models.rate_limit import RateLimitBucket
from api.models.refresh_token import RefreshToken
from api.models.user import User
from api.models.workspace import Workspace
from api.models.workspace_member import WorkspaceMember

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
