"""Add configurable per-trigger warning thresholds

warn_days/warn_hours/warn_landings let an admin override how far ahead of
due_date/due_engine_hours/due_landings a MaintenanceTrigger flags as
'due_soon'. NULL preserves the previous hardcoded behaviour (30 days /
interval-derived hours / interval-derived landings), so existing rows need
no backfill.

Revision ID: b99d29d2c62a
Revises: 25d789509139
Create Date: 2026-07-28 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "b99d29d2c62a"
down_revision = "25d789509139"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maintenance_triggers", sa.Column("warn_days", sa.Integer(), nullable=True)
    )
    op.add_column(
        "maintenance_triggers",
        sa.Column("warn_hours", sa.Numeric(6, 1), nullable=True),
    )
    op.add_column(
        "maintenance_triggers", sa.Column("warn_landings", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("maintenance_triggers", "warn_landings")
    op.drop_column("maintenance_triggers", "warn_hours")
    op.drop_column("maintenance_triggers", "warn_days")
