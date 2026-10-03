"""Inbound WebSocket frames; outbound ones are built from validated data."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from api.schemas.document import ProseMirrorDoc


class WsTicketRead(BaseModel):

    ticket: str
    expires_in: int = Field(description="Ticket lifetime in seconds.")


class EditOperation(BaseModel):
    """Steps for peers to replay plus the document they produced, since the
    server cannot run ProseMirror. Trusting `doc` is safe only because
    `base_version` must match the stored version exactly."""

    model_config = ConfigDict(extra="forbid")

    steps: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    doc: ProseMirrorDoc


class EditMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["edit"]
    base_version: int = Field(ge=1)
    operation: EditOperation


class CursorMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["cursor"]
    anchor: int = Field(ge=0)
    head: int = Field(ge=0)


class PingMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ping"]


ClientMessage = Annotated[
    Union[EditMessage, CursorMessage, PingMessage], Field(discriminator="type")
]

CLIENT_MESSAGE_ADAPTER: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)
