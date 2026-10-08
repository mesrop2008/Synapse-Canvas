"""ai queries

Revision ID: 3aa51fe75857
Revises: 3f2a9c1d7e44
Create Date: 2026-10-08 19:24:06.788053
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '3aa51fe75857'
down_revision: Union[str, None] = '3f2a9c1d7e44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ai_query_mode = postgresql.ENUM(
    'continue', 'rewrite', 'summarize', 'ask', name='ai_query_mode', create_type=False
)
ai_query_status = postgresql.ENUM(
    'streaming', 'completed', 'cancelled', 'failed', name='ai_query_status', create_type=False
)


def upgrade() -> None:
    ai_query_mode.create(op.get_bind(), checkfirst=True)
    ai_query_status.create(op.get_bind(), checkfirst=True)

    op.create_table('ai_queries',
    sa.Column('document_id', sa.Uuid(), nullable=False),
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('prompt', sa.Text(), nullable=False),
    sa.Column('mode', ai_query_mode, nullable=False),
    sa.Column('selection_from', sa.Integer(), nullable=True),
    sa.Column('selection_to', sa.Integer(), nullable=True),
    sa.Column('status', ai_query_status, nullable=False),
    sa.Column('response', sa.Text(), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('model', sa.String(length=128), nullable=True),
    sa.Column('prompt_tokens', sa.Integer(), nullable=True),
    sa.Column('completion_tokens', sa.Integer(), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name=op.f('fk_ai_queries_document_id_documents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_ai_queries_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_ai_queries_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_queries'))
    )
    op.create_index('ix_ai_queries_document_id_user_id_created_at', 'ai_queries', ['document_id', 'user_id', sa.literal_column('created_at DESC'), sa.literal_column('id DESC')], unique=False)
    op.create_index('ix_ai_queries_workspace_id_completed_at', 'ai_queries', ['workspace_id', 'completed_at'], unique=False)
    op.create_index('ix_ai_queries_workspace_id_streaming', 'ai_queries', ['workspace_id'], unique=False, postgresql_where=sa.text("status = 'streaming'"))


def downgrade() -> None:
    op.drop_index('ix_ai_queries_workspace_id_streaming', table_name='ai_queries', postgresql_where=sa.text("status = 'streaming'"))
    op.drop_index('ix_ai_queries_workspace_id_completed_at', table_name='ai_queries')
    op.drop_index('ix_ai_queries_document_id_user_id_created_at', table_name='ai_queries')
    op.drop_table('ai_queries')
    ai_query_status.drop(op.get_bind(), checkfirst=True)
    ai_query_mode.drop(op.get_bind(), checkfirst=True)
