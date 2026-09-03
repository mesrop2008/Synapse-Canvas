from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import NonEmptyName, Password


class RegisterRequest(BaseModel):
    email: EmailStr
    password: Password
    name: NonEmptyName


class LoginRequest(BaseModel):
    email: EmailStr
    # Deliberately not `Password`: login must not reveal the password policy,
    # and an old password that predates a policy change must still be able to
    # authenticate.
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
    """Deliberately uninformative body.

    Returned by registration and resend alike, whatever actually happened, so
    the response cannot be used to test whether an address is registered.
    """

    detail: str
