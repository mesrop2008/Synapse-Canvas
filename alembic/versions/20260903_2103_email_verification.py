"""email verification

Revision ID: 8971ea62bc37
Revises: c78fcd71a07a
Create Date: 2026-09-03 21:03:57.107593
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8971ea62bc37'
down_revision: Union[str, None] = 'c78fcd71a07a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('email_verification_tokens',
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_email_verification_tokens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_email_verification_tokens')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_email_verification_tokens_token_hash'))
    )
    op.create_index('ix_email_verification_tokens_expires_at', 'email_verification_tokens', ['expires_at'], unique=False)
    op.create_index(op.f('ix_email_verification_tokens_user_id'), 'email_verification_tokens', ['user_id'], unique=False)
    op.add_column('users', sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True))

    # Grandfather accounts that predate this migration: login now refuses an
    # unverified address, so without this the deploy locks out every existing
    # user. To force them through verification instead, drop this and send the
    # links *before* deploying.
    op.execute(
        sa.text("UPDATE users SET email_verified_at = now() "
                "WHERE email_verified_at IS NULL")
    )


def downgrade() -> None:
    op.drop_column('users', 'email_verified_at')
    op.drop_index(op.f('ix_email_verification_tokens_user_id'), table_name='email_verification_tokens')
    op.drop_index('ix_email_verification_tokens_expires_at', table_name='email_verification_tokens')
    op.drop_table('email_verification_tokens')
