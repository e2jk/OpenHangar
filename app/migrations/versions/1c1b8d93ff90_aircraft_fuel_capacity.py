"""Add fuel tank capacity to aircraft

fuel_capacity_liters is the total usable fuel capacity across all tanks, in
liters. NULL means not configured, which hides the tank-fraction quick-fill
buttons on the flight form. Existing rows need no backfill.

Revision ID: 1c1b8d93ff90
Revises: b99d29d2c62a
Create Date: 2026-08-13 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "1c1b8d93ff90"
down_revision = "b99d29d2c62a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "aircraft",
        sa.Column("fuel_capacity_liters", sa.Numeric(6, 1), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("aircraft", "fuel_capacity_liters")
