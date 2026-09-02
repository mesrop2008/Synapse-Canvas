"""Shared FastAPI dependencies: database session, current user, workspace access.

`WorkspaceAccess` is the piece worth reading. It turns "the caller must be at
least an editor of the workspace named in the path" into a type annotation, so
route handlers contain zero permission code and cannot forget the check --
omitting the dependency also removes the handler's only source of the
workspace object, which makes the mistake fail loudly instead of silently.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.security import ACCESS_TOKEN, decode_token, subject_uuid
from app.db.session import get_db
from app.models.enums import WorkspaceRole
from app.models.user import User
from app.models.workspace import Workspace
from app.services import auth_service, workspace_service

DbSession = Annotated[AsyncSession, Depends(get_db)]

# auto_error=False so a missing header raises our own AuthenticationError and
# produces the same body shape as every other failure in the API.
_bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")


async def get_current_user(
    db: DbSession,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
) -> User:
    if credentials is None:
        raise AuthenticationError("Not authenticated")

    payload = decode_token(credentials.credentials, ACCESS_TOKEN)
    user = await auth_service.get_user_by_id(db, subject_uuid(payload))
    if user is None:
        # Signature was valid but the account is gone -- a token outliving its
        # user must not keep working.
        raise AuthenticationError("User no longer exists")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    """Everything a workspace route needs, resolved once by the dependency."""

    workspace: Workspace
    role: WorkspaceRole
    user: User


class WorkspaceAccess:
    """Dependency factory enforcing a minimum role on `{workspace_id}`.

    Note the two different failure modes, which are deliberately asymmetric:

    * Not a member (or no such workspace) -> 404. Returning 403 here would
      confirm that a workspace with this id exists, letting an outsider probe
      for valid ids. The service query is an inner join, so both cases arrive
      as the same empty result and cannot be told apart even internally.
    * A member, but too junior -> 403. The caller already knows the workspace
      exists, so there is nothing left to hide and a truthful error is more
      useful than a misleading 404.
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
            raise NotFoundError("Workspace not found")

        workspace, role = found
        if not role.satisfies(self.minimum_role):
            raise PermissionDeniedError(
                f"This action requires the '{self.minimum_role}' role or higher; "
                f"your role is '{role}'."
            )
        return WorkspaceContext(workspace=workspace, role=role, user=current_user)


# Ready-made annotations so handlers read as `ctx: RequireOwner`.
RequireViewer = Annotated[WorkspaceContext, Depends(WorkspaceAccess(WorkspaceRole.VIEWER))]
RequireEditor = Annotated[WorkspaceContext, Depends(WorkspaceAccess(WorkspaceRole.EDITOR))]
RequireOwner = Annotated[WorkspaceContext, Depends(WorkspaceAccess(WorkspaceRole.OWNER))]
