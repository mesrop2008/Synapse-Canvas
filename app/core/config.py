"""Application settings, resolved once via cached `get_settings()`.

Depend on `get_settings()` rather than a module-level singleton so tests can
override the environment before first access.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    project_name: str = "Synapse Canvas API"
    environment: Environment = "local"
    debug: bool = False

    database_url: str
    # Separate database for the suite, which drops/creates tables in it.
    test_database_url: str | None = None

    jwt_secret_key: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Retired signing keys (comma-separated): tokens they signed still verify,
    # so the active key rotates without logging everyone out. Drop a key once
    # its tokens have all expired.
    previous_jwt_secret_keys_raw: str = Field(
        default="", alias="PREVIOUS_JWT_SECRET_KEYS"
    )

    jwt_issuer: str = "synapse-canvas"
    jwt_audience: str = "synapse-canvas-api"
    email_verification_expire_hours: int = 24
    # Frontend route that reads the token and POSTs it to /auth/verify-email.
    email_verification_link_base: str = "http://localhost:3000/verify-email"

    # 12 is a sane default; the suite lowers it to 4 so tests aren't KDF-bound.
    bcrypt_rounds: int = 12

    # Throttling, not lockout: a lockout keyed on an account lets anyone lock
    # its owner out. Applied per IP and per account.
    login_rate_limit_per_ip: int = 10
    login_rate_limit_per_ip_window_seconds: int = 300
    login_rate_limit_per_account: int = 5
    login_rate_limit_per_account_window_seconds: int = 900
    register_rate_limit_per_ip: int = 5
    register_rate_limit_per_ip_window_seconds: int = 3600
    refresh_rate_limit_per_ip: int = 30
    refresh_rate_limit_per_ip_window_seconds: int = 300

    # X-Forwarded-For is client-spoofable; only trust it behind a proxy you
    # control, or a client can mint a fresh rate-limit identity per request.
    trust_proxy_headers: bool = False

    # Plain str, not list[str]: pydantic-settings JSON-decodes complex types
    # before validators run, which makes CSV env vars awkward.
    cors_origins_raw: str = Field(default="", alias="CORS_ORIGINS")

    @field_validator("database_url", "test_database_url")
    @classmethod
    def _require_async_driver(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "Database URLs must use the asyncpg driver, "
                f"e.g. postgresql+asyncpg://user:pass@host:5432/db (got: {v!r})"
            )
        return v

    # Refused before the body is read; a proxy should also cap this.
    max_request_body_bytes: int = 1_048_576

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]

    @property
    def previous_jwt_secret_keys(self) -> list[str]:
        return [
            k.strip() for k in self.previous_jwt_secret_keys_raw.split(",") if k.strip()
        ]

    @property
    def is_testing(self) -> bool:
        return self.environment == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _configure_third_party_logging()
    return Settings()  # type: ignore[call-arg]  # values come from the environment


def _configure_third_party_logging() -> None:
    # passlib 1.7.4 probes bcrypt.__about__ (removed in 4.1) and logs a
    # traceback on first use; hashing is unaffected, so silence the noise.
    logging.getLogger("passlib.handlers.bcrypt").setLevel(logging.CRITICAL)
