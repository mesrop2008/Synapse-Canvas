"""document change log

The unique constraint on (document_id, version) is the database-level guarantee
that two writers cannot produce the same version, and its btree also serves the
"replay this document in order" read -- hence no separate index on the pair.

Revision ID: d65cb3ba5854
Revises: 74a5e371e016
Create Date: 2026-09-19 14:47:58.363232
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'd65cb3ba5854'
down_revision: Union[str, None] = '74a5e371e016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('document_changes',
    sa.Column('document_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('base_version', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('operation', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name=op.f('fk_document_changes_document_id_documents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_document_changes_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_document_changes')),
    sa.UniqueConstraint('document_id', 'version', name='uq_document_changes_document_id_version')
    )
    op.create_index(op.f('ix_document_changes_user_id'), 'document_changes', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_document_changes_user_id'), table_name='document_changes')
    op.drop_table('document_changes')
