"""Every model must be imported here, or Alembic never sees it."""

from api.db.base import Base
from api.models.ai_query import AIQuery
from api.models.document import Document
from api.models.document_change import DocumentChange
from api.models.email_verification import EmailVerificationCode
from api.models.enums import WORKSPACE_ROLE_ENUM_NAME, WorkspaceRole
from api.models.rate_limit import RateLimitBucket
from api.models.refresh_token import RefreshToken
from api.models.user import User
from api.models.workspace import Workspace
from api.models.workspace_member import WorkspaceMember

__all__ = [
    "AIQuery",
    "Base",
    "Document",
    "DocumentChange",
    "EmailVerificationCode",
    "RateLimitBucket",
    "RefreshToken",
    "User",
    "Workspace",
    "WorkspaceMember",
    "WorkspaceRole",
    "WORKSPACE_ROLE_ENUM_NAME",
]
