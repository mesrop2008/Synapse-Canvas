"""Application settings, loaded from environment variables / `.env`.

Settings are resolved once and cached (`get_settings`) so that the same object
is shared everywhere. Modules should depend on `get_settings()` rather than
importing a module-level singleton, which keeps tests free to override the
environment before the first access.
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

    # --- General ----------------------------------------------------------
    project_name: str = "Synapse Canvas API"
    environment: Environment = "local"
    debug: bool = False

    # --- Database ---------------------------------------------------------
    # Must use the asyncpg driver: the whole data layer is async.
    database_url: str
    # Separate database for the test suite so `pytest` can drop/create tables
    # without touching development data.
    test_database_url: str | None = None

    # --- Auth -------------------------------------------------------------
    jwt_secret_key: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    # Work factor for bcrypt. 12 is a reasonable 2020s default; lowered to 4
    # in the test environment so the suite is not dominated by KDF time.
    bcrypt_rounds: int = 12

    # --- Abuse resistance -------------------------------------------------
    # Throttling, not account lockout. NIST SP 800-63B recommends throttling
    # precisely because a lockout keyed on an account is itself a denial of
    # service: anyone who knows an address can lock its owner out at will.
    # Limits are applied per client IP *and* per targeted account, so neither
    # a single noisy address nor a single targeted account can be hammered.
    login_rate_limit_per_ip: int = 10
    login_rate_limit_per_ip_window_seconds: int = 300
    login_rate_limit_per_account: int = 5
    login_rate_limit_per_account_window_seconds: int = 900
    register_rate_limit_per_ip: int = 5
    register_rate_limit_per_ip_window_seconds: int = 3600
    refresh_rate_limit_per_ip: int = 30
    refresh_rate_limit_per_ip_window_seconds: int = 300

    # X-Forwarded-For is trivially spoofable by the client, and trusting it
    # blindly lets an attacker mint a fresh rate-limit identity per request.
    # Only enable this when a proxy you control appends the header.
    trust_proxy_headers: bool = False

    # --- HTTP -------------------------------------------------------------
    # Comma-separated in the environment; exposed as a list via `cors_origins`.
    # Kept as a plain `str` because pydantic-settings JSON-decodes complex
    # types before validators run, which makes CSV env vars awkward to type
    # directly as `list[str]`.
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

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]

    @property
    def is_testing(self) -> bool:
        return self.environment == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _configure_third_party_logging()
    return Settings()  # type: ignore[call-arg]  # values come from the environment


def _configure_third_party_logging() -> None:
    """Silence a known-noisy, harmless passlib probe.

    passlib 1.7.4 reads `bcrypt.__about__.__version__` to detect the backend
    version. bcrypt >= 4.1 removed that attribute, so passlib logs a warning
    with a traceback on first use. Hashing and verification are unaffected --
    only the version probe fails -- so the log record is pure noise.
    """
    logging.getLogger("passlib.handlers.bcrypt").setLevel(logging.CRITICAL)
