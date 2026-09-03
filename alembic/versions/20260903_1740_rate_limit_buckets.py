"""Add rate_limit_buckets for authentication throttling

Revision ID: ab0bc8132763
Revises: 0001_initial
Create Date: 2026-09-03 17:40:15.777724
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ab0bc8132763'
down_revision: Union[str, None] = '0001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The unique constraint is what makes the counter safe under
    # concurrency: INSERT ... ON CONFLICT DO UPDATE needs it as the
    # conflict target, so parallel requests increment rather than race.
    op.create_table('rate_limit_buckets',
    sa.Column('bucket_key', sa.String(length=255), nullable=False),
    sa.Column('window_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('request_count', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rate_limit_buckets')),
    sa.UniqueConstraint('bucket_key', 'window_start', name='uq_rate_limit_buckets_key_window')
    )
    op.create_index('ix_rate_limit_buckets_window_start', 'rate_limit_buckets', ['window_start'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_rate_limit_buckets_window_start', table_name='rate_limit_buckets')
    op.drop_table('rate_limit_buckets')
