from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from api.schemas.common import NonEmptyName


def _is_prosemirror_doc(value: dict[str, Any]) -> dict[str, Any]:
    # Content is otherwise opaque; this stops a write no editor could open.
    if value.get("type") != "doc":
        raise ValueError('content must be a ProseMirror document node: {"type": "doc", ...}')
    return value


ProseMirrorDoc = Annotated[dict[str, Any], AfterValidator(_is_prosemirror_doc)]


class DocumentCreate(BaseModel):
    title: NonEmptyName
    content: ProseMirrorDoc | None = Field(
        default=None, description="Omit for an empty document."
    )


class DocumentUpdate(BaseModel):
    version: int = Field(
        ge=1, description="The version this edit was computed against."
    )
    title: NonEmptyName | None = None
    content: ProseMirrorDoc | None = None

    @model_validator(mode="after")
    def _require_a_change(self) -> "DocumentUpdate":
        # Otherwise a body of only `version` would bump it for no edit.
        if self.title is None and self.content is None:
            raise ValueError("Provide title, content, or both")
        return self


class DocumentSummaryRead(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    version: int
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class DocumentRead(DocumentSummaryRead):
    content: dict[str, Any]


class DocumentVersionConflict(BaseModel):
    """`current` lets the losing client re-sync without another request."""

    code: str
    detail: str
    current: DocumentRead
