from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from api.schemas.common import NonEmptyName, Password


class RegisterRequest(BaseModel):
    email: EmailStr
    password: Password
    name: NonEmptyName


class LoginRequest(BaseModel):
    email: EmailStr
    # Not `Password`: login must not reveal the policy, and a password that
    # predates a policy change must still authenticate.
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=1)


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class AcceptedResponse(BaseModel):
    """Returned by registration and resend alike, whatever happened, so it cannot
    be used to test whether an address is registered."""

    detail: str
