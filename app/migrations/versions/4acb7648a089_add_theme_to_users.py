"""Add theme column to users

Revision ID: 4acb7648a089
Revises: c61c4311041c
Create Date: 2026-06-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4acb7648a089"
down_revision: str = "c61c4311041c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("theme", sa.String(8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "theme")
