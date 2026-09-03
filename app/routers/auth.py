"""Authentication endpoints.

Handlers here do request/response translation only. Anything that could be
called from a non-HTTP entry point lives in `app.services.auth_service`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.dependencies import (
    CurrentUser,
    DbSession,
    login_ip_rate_limit,
    refresh_ip_rate_limit,
    register_ip_rate_limit,
)
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, TokenPair
from app.schemas.user import UserRead
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new account",
    dependencies=[Depends(register_ip_rate_limit)],
    responses={
        409: {"description": "Email already registered"},
        429: {"description": "Too many registrations from this address"},
    },
)
async def register(payload: RegisterRequest, db: DbSession) -> UserRead:
    user = await auth_service.register_user(db, payload)
    return UserRead.model_validate(user)


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Exchange credentials for an access/refresh token pair",
    dependencies=[Depends(login_ip_rate_limit)],
    responses={
        401: {"description": "Incorrect email or password"},
        429: {"description": "Too many attempts from this address or for this account"},
    },
)
async def login(payload: LoginRequest, db: DbSession) -> TokenPair:
    user = await auth_service.authenticate_user(db, payload.email, payload.password)
    return await auth_service.issue_token_pair(db, user)


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Exchange a refresh token for a new token pair",
    dependencies=[Depends(refresh_ip_rate_limit)],
    responses={
        401: {"description": "Invalid, expired, or wrong-type token"},
        429: {"description": "Too many refreshes from this address"},
    },
)
async def refresh(payload: RefreshRequest, db: DbSession) -> TokenPair:
    return await auth_service.refresh_token_pair(db, payload.refresh_token)


@router.get(
    "/me",
    response_model=UserRead,
    summary="The authenticated user",
    responses={401: {"description": "Missing or invalid access token"}},
)
async def me(current_user: CurrentUser) -> UserRead:
    return UserRead.model_validate(current_user)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End the session the refresh token belongs to",
)
async def logout(payload: RefreshRequest, db: DbSession) -> None:
    """Always 204, even for a token that is unknown or already revoked.

    Reporting which is which would turn logout into an oracle for probing
    token validity, and the caller's intent is satisfied either way.
    """
    await auth_service.revoke_refresh_token(db, payload.refresh_token)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End every session for the authenticated user",
    responses={401: {"description": "Missing or invalid access token"}},
)
async def logout_all(current_user: CurrentUser, db: DbSession) -> None:
    """The lever to pull after a password change or a suspected compromise.

    Note that already-issued access tokens keep working until they expire --
    see the README on why revocation is enforced at the refresh boundary.
    """
    await auth_service.revoke_all_for_user(db, current_user.id)
