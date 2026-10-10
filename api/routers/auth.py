from __future__ import annotations

from fastapi import APIRouter, Depends, status

from api.dependencies import (
    CurrentUser,
    DbSession,
    RequestLocale,
    login_ip_rate_limit,
    password_reset_confirm_ip_rate_limit,
    password_reset_ip_rate_limit,
    password_reset_verify_ip_rate_limit,
    refresh_ip_rate_limit,
    register_ip_rate_limit,
    verify_email_ip_rate_limit,
)
from api.schemas.auth import (
    AcceptedResponse,
    LoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetGrant,
    PasswordResetRequest,
    PasswordResetVerifyRequest,
    RefreshRequest,
    RegisterRequest,
    ResendVerificationRequest,
    TokenPair,
    VerifyEmailRequest,
)
from api.schemas.user import UserRead
from api.services import auth_service, password_reset

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create an inactive account and email it a verification code",
    dependencies=[Depends(register_ip_rate_limit)],
    responses={
        422: {"description": "Malformed address, or a domain that cannot receive mail"},
        429: {"description": "Too many registrations from this address"},
    },
)
async def register(
    payload: RegisterRequest, db: DbSession, locale: RequestLocale
) -> AcceptedResponse:
    # Always 202: a 409 would reveal which addresses have accounts.
    await auth_service.register_user(db, payload, locale)
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
    payload: ResendVerificationRequest, db: DbSession, locale: RequestLocale
) -> AcceptedResponse:
    # Always 202, and the 429 applies to every address alike.
    await auth_service.resend_verification(db, payload.email, locale)
    return AcceptedResponse(
        detail="If that address needs verifying, a new code is on its way."
    )


@router.post(
    "/password-reset",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email a password reset code",
    dependencies=[Depends(password_reset_ip_rate_limit)],
    responses={
        429: {
            "description": "A code was sent to this address under a minute ago "
            "(rate_limit.exceeded), recovery for it is locked after three wrong "
            "codes (auth.reset_locked), or this IP is over its limit. Retry-After "
            "says when to retry."
        },
    },
)
async def request_password_reset(
    payload: PasswordResetRequest, db: DbSession, locale: RequestLocale
) -> AcceptedResponse:
    # Always 202, and each 429 applies to every address alike.
    await password_reset.request_code(db, payload.email, locale)
    return AcceptedResponse(detail="If the email exists, a code has been sent.")


@router.post(
    "/password-reset/verify",
    response_model=PasswordResetGrant,
    summary="Exchange an emailed reset code for a short-lived reset token",
    dependencies=[Depends(password_reset_verify_ip_rate_limit)],
    responses={
        401: {"description": "Wrong, expired, used-up or unknown code"},
        429: {
            "description": "The third wrong code, which destroys it and locks "
            "recovery for the address (auth.reset_locked); or too many wrong codes "
            "today, or from this IP. Retry-After says when to retry."
        },
    },
)
async def verify_password_reset(
    payload: PasswordResetVerifyRequest, db: DbSession
) -> PasswordResetGrant:
    return await password_reset.verify_code(db, payload.email, payload.code)


@router.post(
    "/password-reset/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set a new password with a reset token, ending every session",
    dependencies=[Depends(password_reset_confirm_ip_rate_limit)],
    responses={
        401: {"description": "Expired or already-used reset token, or not one at all"},
        422: {"description": "The new password does not meet the policy"},
        429: {"description": "Too many attempts from this address"},
    },
)
async def confirm_password_reset(
    payload: PasswordResetConfirmRequest, db: DbSession, locale: RequestLocale
) -> None:
    await password_reset.reset_password(
        db, payload.reset_token, payload.new_password, locale
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
