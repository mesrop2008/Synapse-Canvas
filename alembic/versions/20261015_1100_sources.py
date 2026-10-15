"""sources and source chunks

The embedding column is created as vector(EMBEDDING_DIMENSION), read when
this runs. The API and worker refuse to start if the setting later differs
from the column: vectors of one size cannot be compared with another's, so a
change means migrating the column and re-embedding every source.

Revision ID: c61316a1c67e
Revises: 7c1e0b5a9d21
Create Date: 2026-10-15 11:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

from api.core.config import get_settings

revision: str = 'c61316a1c67e'
down_revision: Union[str, None] = '7c1e0b5a9d21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

source_status = postgresql.ENUM(
    'pending', 'processing', 'ready', 'failed', name='source_status', create_type=False
)


def upgrade() -> None:
    dimension = get_settings().embedding_dimension
    source_status.create(op.get_bind(), checkfirst=True)

    op.create_table('sources',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('uploaded_by', sa.Uuid(), nullable=True),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=127), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('storage_path', sa.String(length=512), nullable=False),
    sa.Column('checksum', sa.String(length=64), nullable=False),
    sa.Column('status', source_status, nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('chunk_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], name=op.f('fk_sources_uploaded_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_sources_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sources')),
    sa.UniqueConstraint('workspace_id', 'checksum', name=op.f('uq_sources_workspace_id_checksum'))
    )
    op.create_index('ix_sources_workspace_id_created_at', 'sources', ['workspace_id', sa.literal_column('created_at DESC')], unique=False)

    op.create_table('source_chunks',
    sa.Column('source_id', sa.Uuid(), nullable=False),
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('chunk_index', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('embedding', VECTOR(dimension), nullable=False),
    sa.Column('embedding_model', sa.String(length=128), nullable=False),
    sa.Column('embedding_dimension', sa.Integer(), nullable=False),
    sa.Column('page_number', sa.Integer(), nullable=True),
    sa.Column('char_start', sa.Integer(), nullable=False),
    sa.Column('char_end', sa.Integer(), nullable=False),
    sa.Column('token_count', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], name=op.f('fk_source_chunks_source_id_sources'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_source_chunks_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_source_chunks')),
    sa.UniqueConstraint('source_id', 'chunk_index', name=op.f('uq_source_chunks_source_id_chunk_index'))
    )
    op.create_index('ix_source_chunks_workspace_id', 'source_chunks', ['workspace_id'], unique=False)
    op.create_index('ix_source_chunks_embedding', 'source_chunks', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})


def downgrade() -> None:
    op.drop_index('ix_source_chunks_embedding', table_name='source_chunks', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_index('ix_source_chunks_workspace_id', table_name='source_chunks')
    op.drop_table('source_chunks')
    op.drop_index('ix_sources_workspace_id_created_at', table_name='sources')
    op.drop_table('sources')
    source_status.drop(op.get_bind(), checkfirst=True)
