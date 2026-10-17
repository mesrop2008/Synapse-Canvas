from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, TimestampMixin


class SourceText(TimestampMixin, Base):
    """The text extracted from a source, which its passages' `char_start` and
    `char_end` index into. Its own table, so listing sources never loads it."""

    __tablename__ = "source_texts"

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Offsets into `text` where each page starts; empty when the format has
    # no pages.
    page_starts: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), nullable=False, server_default="{}"
    )
