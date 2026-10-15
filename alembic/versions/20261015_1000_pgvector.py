"""pgvector

Needs an image that ships the extension (see docker-compose.yml) and a role
allowed to create it; the compose user is a superuser.

Revision ID: 7c1e0b5a9d21
Revises: 5311f9d494d2
Create Date: 2026-10-15 10:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = '7c1e0b5a9d21'
down_revision: Union[str, None] = '5311f9d494d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
