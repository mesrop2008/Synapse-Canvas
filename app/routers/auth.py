"""Authentication endpoints.

Handlers here do request/response translation only. Anything that could be
called from a non-HTTP entry point lives in `app.services.auth_service`.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.dependencies import CurrentUser, DbSession
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, TokenPair
from app.schemas.user import UserRead
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new account",
    responses={409: {"description": "Email already registered"}},
)
async def register(payload: RegisterRequest, db: DbSession) -> UserRead:
    user = await auth_service.register_user(db, payload)
    return UserRead.model_validate(user)


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Exchange credentials for an access/refresh token pair",
    responses={401: {"description": "Incorrect email or password"}},
)
async def login(payload: LoginRequest, db: DbSession) -> TokenPair:
    user = await auth_service.authenticate_user(db, payload.email, payload.password)
    return auth_service.issue_token_pair(user)


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Exchange a refresh token for a new token pair",
    responses={401: {"description": "Invalid, expired, or wrong-type token"}},
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
