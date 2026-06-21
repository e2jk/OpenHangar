"""Add cross_country column to pilot_logbook_entries

Revision ID: c61c4311041c
Revises: 6b784bed5c65
Create Date: 2026-06-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c61c4311041c"
down_revision: str = "6b784bed5c65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pilot_logbook_entries",
        sa.Column("cross_country", sa.Numeric(4, 1), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pilot_logbook_entries", "cross_country")
