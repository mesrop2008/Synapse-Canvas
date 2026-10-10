from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, EmailStr, Field

from api.schemas.common import NewEmail, NonEmptyName, Password

# [0-9], not \d: pydantic's regex engine treats \d as any Unicode digit.
EmailedCode = Annotated[
    str,
    Field(
        pattern=r"^[0-9]{6}$",
        description="The six-digit code from the email",
        examples=["042917"],
    ),
]


class RegisterRequest(BaseModel):
    email: NewEmail
    password: Password
    name: NonEmptyName


class LoginRequest(BaseModel):
    email: EmailStr
    # Not `Password`: older passwords must still work, and the policy stays hidden.
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")


class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: EmailedCode


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetVerifyRequest(BaseModel):
    email: EmailStr
    code: EmailedCode


class PasswordResetGrant(BaseModel):
    reset_token: str
    expires_in: int = Field(description="Reset token lifetime in seconds")


class AcceptedResponse(BaseModel):
    """The same whatever happened, so it reveals no registration."""

    detail: str
