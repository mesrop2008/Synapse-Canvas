from __future__ import annotations

from fastapi import APIRouter, Depends, status

from api.dependencies import (
    CurrentUser,
    DbSession,
    login_ip_rate_limit,
    refresh_ip_rate_limit,
    register_ip_rate_limit,
    verify_email_ip_rate_limit,
)
from api.schemas.auth import (
    AcceptedResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    ResendVerificationRequest,
    TokenPair,
    VerifyEmailRequest,
)
from api.schemas.user import UserRead
from api.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create an inactive account and email it a verification code",
    dependencies=[Depends(register_ip_rate_limit)],
    responses={
        429: {"description": "Too many registrations from this address"},
    },
)
async def register(payload: RegisterRequest, db: DbSession) -> AcceptedResponse:
    # Always 202: a 409 would reveal which addresses have accounts.
    await auth_service.register_user(db, payload)
    return AcceptedResponse(
        detail="If that address can receive mail, a verification code is on its way."
    )


@router.post(
    "/verify-email",
    response_model=UserRead,
    summary="Redeem an emailed verification code and activate the account",
    dependencies=[Depends(verify_email_ip_rate_limit)],
    responses={
        401: {"description": "Wrong, expired, used-up or unknown code"},
        429: {"description": "Too many attempts from this address or for this account"},
    },
)
async def verify_email(payload: VerifyEmailRequest, db: DbSession) -> UserRead:
    user = await auth_service.verify_email(db, payload.email, payload.code)
    return UserRead.model_validate(user)


@router.post(
    "/resend-verification",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request a fresh verification code",
    dependencies=[Depends(register_ip_rate_limit)],
    responses={
        429: {
            "description": "A code was sent to this address under a minute ago, "
            "or too many have been sent this hour. Retry-After says when to retry."
        },
    },
)
async def resend_verification(
    payload: ResendVerificationRequest, db: DbSession
) -> AcceptedResponse:
    # Always 202, and the 429 applies to every address alike.
    await auth_service.resend_verification(db, payload.email)
    return AcceptedResponse(
        detail="If that address needs verifying, a new code is on its way."
    )


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Exchange credentials for an access/refresh token pair",
    dependencies=[Depends(login_ip_rate_limit)],
    responses={
        401: {"description": "Incorrect email or password"},
        403: {"description": "Email address has not been verified"},
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
    # Always 204: logout is not an oracle.
    await auth_service.revoke_refresh_token(db, payload.refresh_token)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End every session for the authenticated user",
    responses={401: {"description": "Missing or invalid access token"}},
)
async def logout_all(current_user: CurrentUser, db: DbSession) -> None:
    # Access tokens already issued live out their 30 minutes.
    await auth_service.revoke_all_for_user(db, current_user.id)
