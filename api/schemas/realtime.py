"""Inbound WebSocket frames.

Only the client's messages are modelled. What the server sends is assembled
where it is sent, because every field of it comes from a row or a Redis entry
that has already been validated.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from api.schemas.document import ProseMirrorDoc


class WsTicketRead(BaseModel):
    """Response of `POST /documents/{id}/ws-ticket`."""

    ticket: str
    expires_in: int = Field(description="Ticket lifetime in seconds.")


class EditOperation(BaseModel):
    """A batch of ProseMirror steps, and the document they produced.

    The steps are what peers replay; `doc` is what the server stores. The server
    cannot run ProseMirror to derive one from the other -- that is a JavaScript
    library -- so the client sends both. Taking the client's word for the result
    is safe only because `base_version` has to match the stored version exactly,
    which means it computed that result from the same bytes the server holds.

    The cost is a whole document on the wire per edit, which is why the client
    batches transactions before sending. A production build would run the schema
    server-side (a Node sidecar, or a CRDT) and send steps alone.
    """

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
