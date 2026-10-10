"""user token version

Existing rows start at 0, the version tokens issued before this carry
implicitly, so nobody is signed out by the upgrade.

Revision ID: 5311f9d494d2
Revises: 3aa51fe75857
Create Date: 2026-10-10 12:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '5311f9d494d2'
down_revision: Union[str, None] = '3aa51fe75857'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('token_version', sa.Integer(), server_default=sa.text('0'), nullable=False))


def downgrade() -> None:
    op.drop_column('users', 'token_version')
