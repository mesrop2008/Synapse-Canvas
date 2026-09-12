"""documents

`version` carries a server default as well as a model default, so a row
inserted outside the app is still usable by the optimistic-concurrency UPDATE.

Revision ID: 74a5e371e016
Revises: 8971ea62bc37
Create Date: 2026-09-12 11:59:19.606915
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '74a5e371e016'
down_revision: Union[str, None] = '8971ea62bc37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('documents',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_documents_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_documents_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_documents'))
    )
    op.create_index(op.f('ix_documents_created_by'), 'documents', ['created_by'], unique=False)
    op.create_index('ix_documents_workspace_id_updated_at', 'documents', ['workspace_id', sa.literal_column('updated_at DESC')], unique=False)


def downgrade() -> None:
    op.drop_index('ix_documents_workspace_id_updated_at', table_name='documents')
    op.drop_index(op.f('ix_documents_created_by'), table_name='documents')
    op.drop_table('documents')
