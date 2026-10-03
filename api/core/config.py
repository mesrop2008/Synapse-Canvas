"""Read through `get_settings()`, not a module-level instance, so tests can set
the environment before first access. Each setting is documented in .env.example."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]
EmailBackend = Literal["console", "smtp"]
SmtpSecurity = Literal["starttls", "tls", "none"]


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
    test_database_url: str | None = None

    redis_url: str = "redis://localhost:6379/0"

    jwt_secret_key: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    previous_jwt_secret_keys_raw: str = Field(
        default="", alias="PREVIOUS_JWT_SECRET_KEYS"
    )
    jwt_issuer: str = "synapse-canvas"
    jwt_audience: str = "synapse-canvas-api"

    email_verification_code_ttl_seconds: int = 300
    email_verification_max_attempts: int = 5
    email_verification_resend_cooldown_seconds: int = 60
    email_verification_send_limit: int = 5
    email_verification_send_limit_window_seconds: int = 3600
    email_verification_failure_limit: int = 20
    email_verification_failure_limit_window_seconds: int = 86400

    email_backend: EmailBackend = "console"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_security: SmtpSecurity = "starttls"
    smtp_timeout_seconds: float = 15.0
    mail_from_address: str = ""
    mail_from_name: str = "Synapse Canvas"

    bcrypt_rounds: int = 12

    login_rate_limit_per_ip: int = 10
    login_rate_limit_per_ip_window_seconds: int = 300
    login_rate_limit_per_account: int = 5
    login_rate_limit_per_account_window_seconds: int = 900
    register_rate_limit_per_ip: int = 5
    register_rate_limit_per_ip_window_seconds: int = 3600
    refresh_rate_limit_per_ip: int = 30
    refresh_rate_limit_per_ip_window_seconds: int = 300
    verify_email_rate_limit_per_ip: int = 30
    verify_email_rate_limit_per_ip_window_seconds: int = 300
    trust_proxy_headers: bool = False

    ws_ticket_ttl_seconds: int = 30
    ws_heartbeat_interval_seconds: int = 10
    ws_idle_timeout_seconds: int = 45
    ws_max_message_bytes: int = 262_144
    ws_edit_rate_limit: int = 60
    ws_edit_rate_limit_window_seconds: int = 10
    presence_ttl_seconds: int = 30

    # Comma-separated str, not list[str]: pydantic-settings JSON-decodes list
    # types before validators run.
    cors_origins_raw: str = Field(default="", alias="CORS_ORIGINS")

    max_request_body_bytes: int = 1_048_576

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

    @model_validator(mode="after")
    def _check_mail_settings(self) -> "Settings":
        if self.email_backend == "console":
            if self.environment not in ("local", "test"):
                raise ValueError(
                    "EMAIL_BACKEND=console logs every verification code and is "
                    "only allowed when ENVIRONMENT is local or test. Configure "
                    "EMAIL_BACKEND=smtp and the SMTP_* settings."
                )
            return self

        # Gmail, Yandex and Mail.ru only send as the mailbox you log in as.
        if not self.mail_from_address and "@" in self.smtp_username:
            self.mail_from_address = self.smtp_username
        if not self.smtp_host or not self.mail_from_address:
            raise ValueError(
                "EMAIL_BACKEND=smtp needs SMTP_HOST, and MAIL_FROM_ADDRESS unless "
                "SMTP_USERNAME is an email address."
            )
        if self.smtp_security == "none" and self.smtp_username:
            raise ValueError(
                "SMTP_SECURITY=none would send SMTP_PASSWORD in the clear. Use "
                "starttls (port 587) or tls (port 465)."
            )
        return self

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
    # passlib probes bcrypt.__about__ (gone since 4.1) and logs a harmless
    # traceback on first use.
    logging.getLogger("passlib.handlers.bcrypt").setLevel(logging.CRITICAL)
