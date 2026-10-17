"""source texts, for the source viewer

Revision ID: 4f2a9c7e1b30
Revises: 9cf352aa94b7
Create Date: 2026-10-18 10:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '4f2a9c7e1b30'
down_revision: Union[str, None] = '9cf352aa94b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Sources read before this have no row; the viewer reads them again the
    # first time one is opened.
    op.create_table(
        'source_texts',
        sa.Column('source_id', sa.Uuid(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('page_starts', postgresql.ARRAY(sa.Integer()), server_default='{}', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('source_id'),
    )


def downgrade() -> None:
    op.drop_table('source_texts')
