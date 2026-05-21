"""add app_settings table

Revision ID: b7e3d1f09c22
Revises: a1b9f3c82d55
Create Date: 2026-05-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e3d1f09c22"
down_revision: str | None = "a1b9f3c82d55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
