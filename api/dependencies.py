"""`WorkspaceAccess` turns a role requirement into a type annotation. It is also
the handler's only source of the workspace object, so omitting the check fails
loudly rather than silently."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.exceptions import (
    AuthenticationError,
    ErrorCode,
    NotFoundError,
    PermissionDeniedError,
)
from api.core.security import ACCESS_TOKEN, decode_token, subject_uuid
from api.db.session import get_db
from api.models.enums import WorkspaceRole
from api.models.user import User
from api.models.workspace import Workspace
from api.services import auth_service, rate_limit_service, workspace_service

DbSession = Annotated[AsyncSession, Depends(get_db)]

# auto_error=False so a missing header raises our own AuthenticationError, and
# the body shape matches every other failure.
_bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")


async def get_current_user(
    db: DbSession,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
) -> User:
    if credentials is None:
        raise AuthenticationError(
            "Not authenticated", code=ErrorCode.NOT_AUTHENTICATED
        )

    payload = decode_token(credentials.credentials, ACCESS_TOKEN)
    user = await auth_service.get_user_by_id(db, subject_uuid(payload))
    if user is None:
        # Valid signature, deleted account: the token must stop working.
        raise AuthenticationError("User no longer exists", code=ErrorCode.USER_GONE)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    """Everything a workspace route needs, resolved once by the dependency."""

    workspace: Workspace
    role: WorkspaceRole
    user: User


class WorkspaceAccess:
    """Enforces a minimum role on `{workspace_id}`.

    Non-member or no such workspace both give 404 -- one empty inner-join result,
    so ids cannot be probed. A member below the required role gets 403, since
    they already know it exists.
    """

    def __init__(self, minimum_role: WorkspaceRole) -> None:
        self.minimum_role = minimum_role

    async def __call__(
        self,
        workspace_id: uuid.UUID,
        db: DbSession,
        current_user: CurrentUser,
    ) -> WorkspaceContext:
        found = await workspace_service.get_workspace_with_role(
            db, workspace_id, current_user.id
        )
        if found is None:
            raise NotFoundError(
                "Workspace not found", code=ErrorCode.WORKSPACE_NOT_FOUND
            )

        workspace, role = found
        if not role.satisfies(self.minimum_role):
            raise PermissionDeniedError(
                f"This action requires the '{self.minimum_role}' role or higher; "
                f"your role is '{role}'.",
                code=ErrorCode.ROLE_TOO_LOW,
            )
        return WorkspaceContext(workspace=workspace, role=role, user=current_user)


RequireViewer = Annotated[WorkspaceContext, Depends(WorkspaceAccess(WorkspaceRole.VIEWER))]
RequireEditor = Annotated[WorkspaceContext, Depends(WorkspaceAccess(WorkspaceRole.EDITOR))]
RequireOwner = Annotated[WorkspaceContext, Depends(WorkspaceAccess(WorkspaceRole.OWNER))]


def client_ip(request: Request) -> str:
    """X-Forwarded-For is client-set, so it is honoured only behind a proxy we
    trust -- otherwise a client mints a new rate-limit identity per request."""
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            first_hop = forwarded.split(",")[0].strip()
            if first_hop:
                return first_hop
    return request.client.host if request.client else "unknown"


class IPRateLimit:
    """Limits are read at call time so tests and deployments can change them;
    <= 0 disables the throttle."""

    def __init__(self, prefix: str, limit_attr: str, window_attr: str) -> None:
        self.prefix = prefix
        self.limit_attr = limit_attr
        self.window_attr = window_attr

    async def __call__(self, request: Request, db: DbSession) -> None:
        settings = get_settings()
        limit = int(getattr(settings, self.limit_attr))
        if limit <= 0:
            return
        window = int(getattr(settings, self.window_attr))
        await rate_limit_service.enforce(
            db, rate_limit_service.ip_key(self.prefix, client_ip(request)), limit, window
        )


login_ip_rate_limit = IPRateLimit(
    "login", "login_rate_limit_per_ip", "login_rate_limit_per_ip_window_seconds"
)
register_ip_rate_limit = IPRateLimit(
    "register", "register_rate_limit_per_ip", "register_rate_limit_per_ip_window_seconds"
)
refresh_ip_rate_limit = IPRateLimit(
    "refresh", "refresh_rate_limit_per_ip", "refresh_rate_limit_per_ip_window_seconds"
)
