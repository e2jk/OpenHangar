"""Phase 39g: reserve/overhaul fund contribution (stretch goal)

Adds two nullable Aircraft columns for the co-owner reserve fund — at
most one is ever set (hourly XOR monthly), validated on the owners form.

Revision ID: bf4d67d9f886
Revises: 486e69567b81
Create Date: 2026-07-26 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "bf4d67d9f886"
down_revision = "486e69567b81"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "aircraft",
        sa.Column("reserve_contribution_hourly", sa.Numeric(8, 2), nullable=True),
    )
    op.add_column(
        "aircraft",
        sa.Column("reserve_contribution_monthly", sa.Numeric(8, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("aircraft", "reserve_contribution_monthly")
    op.drop_column("aircraft", "reserve_contribution_hourly")
