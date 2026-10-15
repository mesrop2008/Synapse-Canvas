"""ai query citations and the sources mode

Revision ID: 9cf352aa94b7
Revises: c8132f92fc8b
Create Date: 2026-10-15 13:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '9cf352aa94b7'
down_revision: Union[str, None] = 'c8132f92fc8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Allowed in a transaction since Postgres 12, as long as nothing in the
    # same transaction uses the new value.
    op.execute("ALTER TYPE ai_query_mode ADD VALUE IF NOT EXISTS 'sources'")
    op.add_column('ai_queries', sa.Column('retrieved_chunk_ids', postgresql.ARRAY(sa.Uuid()), nullable=True))
    op.add_column('ai_queries', sa.Column('citations', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('ai_queries', 'citations')
    op.drop_column('ai_queries', 'retrieved_chunk_ids')
    # Postgres cannot drop an enum value; the type is rebuilt without it, and
    # sources-mode queries become plain asks.
    op.execute("UPDATE ai_queries SET mode = 'ask' WHERE mode = 'sources'")
    op.execute("ALTER TYPE ai_query_mode RENAME TO ai_query_mode_old")
    op.execute("CREATE TYPE ai_query_mode AS ENUM ('continue', 'rewrite', 'summarize', 'ask')")
    op.execute(
        "ALTER TABLE ai_queries ALTER COLUMN mode TYPE ai_query_mode "
        "USING mode::text::ai_query_mode"
    )
    op.execute("DROP TYPE ai_query_mode_old")
