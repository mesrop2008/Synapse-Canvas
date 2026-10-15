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
LLMProviderName = Literal["fake", "gemini"]
EmbeddingProviderName = Literal["gemini", "local", "fake"]


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
    email_check_deliverability: bool = True
    email_dns_servers_raw: str = Field(default="", alias="EMAIL_DNS_SERVERS")

    password_reset_code_ttl_seconds: int = 300
    password_reset_max_attempts: int = 3
    password_reset_resend_cooldown_seconds: int = 60
    password_reset_lockout_seconds: int = 300
    password_reset_failure_limit: int = 20
    password_reset_failure_limit_window_seconds: int = 86400
    password_reset_token_ttl_seconds: int = 300

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
    password_reset_rate_limit_per_ip: int = 10
    password_reset_rate_limit_per_ip_window_seconds: int = 3600
    password_reset_verify_rate_limit_per_ip: int = 30
    password_reset_verify_rate_limit_per_ip_window_seconds: int = 300
    trust_proxy_headers: bool = False

    ws_ticket_ttl_seconds: int = 30
    ws_heartbeat_interval_seconds: int = 10
    ws_idle_timeout_seconds: int = 45
    ws_max_message_bytes: int = 262_144
    ws_edit_rate_limit: int = 60
    ws_edit_rate_limit_window_seconds: int = 10
    presence_ttl_seconds: int = 30

    llm_provider: LLMProviderName = "fake"
    # SecretStr keeps the key out of reprs, tracebacks and settings dumps.
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-2.5-flash"
    # 0 turns thinking off on Flash models; -1 lets the model decide.
    gemini_thinking_budget: int = 0
    llm_timeout_seconds: float = 60.0
    fake_llm_delay_seconds: float = 0.04

    ai_max_output_tokens: int = 1024
    ai_context_token_limit: int = 6000
    ai_daily_token_limit: int = 200_000
    ai_max_concurrent_queries: int = 2
    ai_stream_keepalive_seconds: float = 15.0
    ai_reconnect_grace_seconds: float = 10.0
    ai_buffer_ttl_seconds: int = 600
    ai_query_timeout_seconds: int = 300

    embedding_provider: EmbeddingProviderName = "gemini"
    gemini_embedding_model: str = "gemini-embedding-001"
    local_embedding_model: str = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    # Must equal the vector(N) of source_chunks.embedding; checked at startup.
    embedding_dimension: int = 1536
    embedding_timeout_seconds: float = 60.0

    # Relative to the working directory; the containers use a volume.
    source_storage_dir: str = "var/sources"
    source_max_file_bytes: int = 20 * 1024 * 1024
    source_workspace_quota_bytes: int = 200 * 1024 * 1024
    # What a DOCX may expand to; a zip bomb weighs little and expands to gigabytes.
    source_max_uncompressed_bytes: int = 100 * 1024 * 1024
    # Parsing runs in a child process that is killed past the timeout, and on
    # Linux capped at the memory below.
    source_extract_isolated: bool = True
    source_extract_timeout_seconds: float = 120.0
    source_extract_memory_bytes: int = 1024 * 1024 * 1024
    # A source pending this long has lost its job and is queued again.
    source_requeue_after_seconds: int = 60

    # By the embedding provider's count. The cap stays under its input limit.
    chunk_target_tokens: int = 600
    chunk_overlap_tokens: int = 80
    chunk_max_tokens: int = 1000
    embedding_batch_size: int = 50
    embedding_max_retries: int = 4
    embedding_backoff_base_seconds: float = 2.0
    embedding_backoff_max_seconds: float = 60.0
    # Per workspace per UTC day, retries included; metered providers only.
    embedding_daily_request_limit: int = 300

    worker_max_jobs: int = 2
    worker_job_timeout_seconds: int = 1800

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
    def _check_llm_provider(self) -> "Settings":
        if self.llm_provider == "fake" and self.environment not in ("local", "test"):
            raise ValueError(
                "LLM_PROVIDER=fake returns scripted text and is only allowed when "
                "ENVIRONMENT is local or test. Set LLM_PROVIDER=gemini and "
                "GEMINI_API_KEY."
            )
        return self

    @model_validator(mode="after")
    def _check_embedding_settings(self) -> "Settings":
        # Here, not at the top: api.embeddings imports this module.
        from api.embeddings.base import HNSW_MAX_DIMENSIONS
        from api.embeddings.local import LOCAL_EMBEDDING_DIMENSION

        if self.embedding_provider == "fake" and self.environment not in ("local", "test"):
            raise ValueError(
                "EMBEDDING_PROVIDER=fake makes vectors from word hashes and is only "
                "allowed when ENVIRONMENT is local or test. Use gemini, or local to "
                "run without a key."
            )
        if not 1 <= self.embedding_dimension <= HNSW_MAX_DIMENSIONS:
            raise ValueError(
                f"EMBEDDING_DIMENSION={self.embedding_dimension}, but pgvector's HNSW "
                f"index takes at most {HNSW_MAX_DIMENSIONS} dimensions."
            )
        # gemini-embedding-001 returns 128 to 3072 dimensions.
        if self.embedding_provider == "gemini" and self.embedding_dimension < 128:
            raise ValueError("Gemini embeddings have at least 128 dimensions.")
        if (
            self.embedding_provider == "local"
            and self.embedding_dimension != LOCAL_EMBEDDING_DIMENSION
        ):
            raise ValueError(
                f"The local embedding model has {LOCAL_EMBEDDING_DIMENSION} "
                f"dimensions; set EMBEDDING_DIMENSION={LOCAL_EMBEDDING_DIMENSION}. "
                "Changing it on a database that already holds chunks needs a "
                "re-embed (see README)."
            )
        return self

    @model_validator(mode="after")
    def _check_chunking(self) -> "Settings":
        if not 0 <= self.chunk_overlap_tokens < self.chunk_target_tokens <= self.chunk_max_tokens:
            raise ValueError(
                "Chunk sizes must satisfy 0 <= CHUNK_OVERLAP_TOKENS < "
                "CHUNK_TARGET_TOKENS <= CHUNK_MAX_TOKENS."
            )
        # Gemini takes at most 100 texts per batch request.
        if not 1 <= self.embedding_batch_size <= 100:
            raise ValueError("EMBEDDING_BATCH_SIZE must be between 1 and 100.")
        return self

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
    def email_dns_servers(self) -> list[str]:
        return [s.strip() for s in self.email_dns_servers_raw.split(",") if s.strip()]

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
