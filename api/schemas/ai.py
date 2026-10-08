from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from api.services.ai_usage import WorkspaceUsage


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
