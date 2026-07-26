"""Landings-based maintenance triggers

Adds a `landings` MaintenanceTrigger type (due_landings/interval_landings,
mirroring the existing due_engine_hours/interval_hours pair) and a
landings_at_service column on MaintenanceRecord to record the cumulative
landing count at time of service, mirroring hobbs_at_service.

Revision ID: 517626af44f3
Revises: 893349d2be22
Create Date: 2026-07-26 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "517626af44f3"
down_revision = "893349d2be22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maintenance_triggers", sa.Column("due_landings", sa.Integer(), nullable=True)
    )
    op.add_column(
        "maintenance_triggers",
        sa.Column("interval_landings", sa.Integer(), nullable=True),
    )
    op.add_column(
        "maintenance_records",
        sa.Column("landings_at_service", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("maintenance_records", "landings_at_service")
    op.drop_column("maintenance_triggers", "interval_landings")
    op.drop_column("maintenance_triggers", "due_landings")
