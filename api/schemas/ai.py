from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.models.enums import AIQueryMode, AIQueryStatus
from api.services.ai_usage import WorkspaceUsage


class AIQueryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: AIQueryMode
    instruction: str = Field(default="", max_length=2000)
    selection_from: int | None = Field(
        default=None, ge=0, description="ProseMirror position; required for rewrite."
    )
    selection_to: int | None = Field(
        default=None,
        ge=0,
        description="ProseMirror position. For continue, the cursor; omit for the end.",
    )

    @model_validator(mode="after")
    def _mode_needs(self) -> AIQueryCreate:
        if self.mode is AIQueryMode.ASK and not self.instruction.strip():
            raise ValueError("Ask needs a question in `instruction`")
        if self.mode is AIQueryMode.REWRITE and (
            self.selection_from is None or self.selection_to is None
        ):
            raise ValueError("Rewrite needs selection_from and selection_to")
        return self


class AIQueryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID | None
    prompt: str
    mode: AIQueryMode
    selection_from: int | None
    selection_to: int | None
    status: AIQueryStatus
    response: str | None
    error_code: str | None
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    created_at: datetime
    completed_at: datetime | None


class AIQueryApply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, description="The document version the client sees.")
    selection_from: int | None = Field(
        default=None,
        ge=0,
        description="The stored range, mapped through edits since; omit to use it as is.",
    )
    selection_to: int | None = Field(default=None, ge=0)


class AIQueryPage(BaseModel):
    items: list[AIQueryRead]
    next_cursor: str | None = Field(
        description="Pass back as `cursor` for the next page; null on the last."
    )


class WorkspaceUsageRead(BaseModel):
    day: date = Field(description="The UTC day the counts cover.")
    tokens_used: int
    tokens_remaining: int
    daily_token_limit: int = Field(description="0 means no daily limit.")
    streaming_queries: int
    max_concurrent_queries: int = Field(description="0 means no limit.")
    resets_at: datetime

    @classmethod
    def of(cls, usage: WorkspaceUsage) -> WorkspaceUsageRead:
        return cls(
            day=usage.day,
            tokens_used=usage.tokens_used,
            tokens_remaining=usage.tokens_remaining,
            daily_token_limit=usage.daily_token_limit,
            streaming_queries=usage.streaming_queries,
            max_concurrent_queries=usage.max_concurrent_queries,
            resets_at=usage.resets_at,
        )
