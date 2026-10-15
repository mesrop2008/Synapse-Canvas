"""embedding requests

Revision ID: c8132f92fc8b
Revises: c61316a1c67e
Create Date: 2026-10-15 12:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c8132f92fc8b'
down_revision: Union[str, None] = 'c61316a1c67e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('embedding_requests',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('source_id', sa.Uuid(), nullable=True),
    sa.Column('purpose', sa.String(length=16), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('texts', sa.Integer(), nullable=False),
    sa.Column('tokens', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], name=op.f('fk_embedding_requests_source_id_sources'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_embedding_requests_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_embedding_requests'))
    )
    op.create_index('ix_embedding_requests_workspace_id_created_at', 'embedding_requests', ['workspace_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_embedding_requests_workspace_id_created_at', table_name='embedding_requests')
    op.drop_table('embedding_requests')
