"""Declarative base, shared metadata and column mixins."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# PostgreSQL auto-generates names for unnamed constraints and indexes. Those
# names end up in migrations, and they are not reproducible across databases,
# which makes a later `ALTER`/`DROP` in a migration a guessing game. Fixing a
# naming convention up front makes every constraint name deterministic and
# lets Alembic autogenerate emit correct drops.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    """UUID primary key, generated client-side.

    Generating in Python rather than with a server-side `gen_random_uuid()`
    means SQLAlchemy already knows the key it is inserting, so it does not
    need a RETURNING round trip to learn it.

    The default is evaluated during flush, not at construction: a freshly
    built instance still has `id is None` until it is flushed.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    """`created_at` as a timezone-aware timestamp, defaulted by the database.

    `server_default=now()` keeps the clock authoritative on the database side,
    so rows written by migrations or by psql get a sane value too.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
