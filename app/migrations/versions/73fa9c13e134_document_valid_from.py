"""Document valid_from

Revision ID: 73fa9c13e134
Revises: e904c15b49df
Create Date: 2026-08-21 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "73fa9c13e134"
down_revision = "e904c15b49df"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("valid_from", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "valid_from")
