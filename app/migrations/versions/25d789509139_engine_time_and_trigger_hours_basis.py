"""Add flights.engine_time and maintenance_triggers.hours_basis

engine_time is the raw engine-hours duration for a flight (engine counter
end minus start, no offset), distinct from flight_time which approximates
the airborne segment. hours_basis lets an HOURS-type MaintenanceTrigger be
measured against engine hours (default, matches prior behaviour) or flight
hours instead — engine/propeller TBO tracking now sums engine_time rather
than flight_time (see services/component_limits.py).

Revision ID: 25d789509139
Revises: 7e90492c58df
Create Date: 2026-07-28 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "25d789509139"
down_revision = "7e90492c58df"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("flights", sa.Column("engine_time", sa.Numeric(4, 1), nullable=True))
    op.add_column(
        "maintenance_triggers",
        sa.Column(
            "hours_basis",
            sa.String(16),
            nullable=False,
            server_default="engine",
        ),
    )


def downgrade() -> None:
    op.drop_column("maintenance_triggers", "hours_basis")
    op.drop_column("flights", "engine_time")
