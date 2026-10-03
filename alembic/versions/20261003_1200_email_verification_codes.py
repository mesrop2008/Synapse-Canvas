"""email verification codes

Replaces the emailed link tokens with six-digit codes: one row per user,
holding an HMAC of the code, its expiry and the wrong attempts made against it.

Outstanding links stop working. They were single-use and short-lived, and an
affected user requests a code from the verification page.

Revision ID: 3f2a9c1d7e44
Revises: d65cb3ba5854
Create Date: 2026-10-03 12:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3f2a9c1d7e44'
down_revision: Union[str, None] = 'd65cb3ba5854'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('ix_email_verification_tokens_expires_at', table_name='email_verification_tokens')
    op.drop_index(op.f('ix_email_verification_tokens_user_id'), table_name='email_verification_tokens')
    op.drop_table('email_verification_tokens')

    op.create_table('email_verification_codes',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('failed_attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_email_verification_codes_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_email_verification_codes')),
    sa.UniqueConstraint('user_id', name=op.f('uq_email_verification_codes_user_id'))
    )
    op.create_index('ix_email_verification_codes_expires_at', 'email_verification_codes', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_email_verification_codes_expires_at', table_name='email_verification_codes')
    op.drop_table('email_verification_codes')

    # Recreated empty: a code cannot be turned back into a link.
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
