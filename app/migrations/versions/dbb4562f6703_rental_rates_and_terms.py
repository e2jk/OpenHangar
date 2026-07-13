"""Phase 37b: rental rates & terms (AircraftBookingSettings extension)

Revision ID: dbb4562f6703
Revises: 722deeb814e0
Create Date: 2026-07-14 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "dbb4562f6703"
down_revision = "722deeb814e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "aircraft_booking_settings",
        sa.Column(
            "rate_basis",
            sa.String(length=16),
            nullable=False,
            server_default="engine_time",
        ),
    )
    op.add_column(
        "aircraft_booking_settings",
        sa.Column(
            "rate_type", sa.String(length=8), nullable=False, server_default="wet"
        ),
    )
    op.add_column(
        "aircraft_booking_settings",
        sa.Column("min_hours_per_day", sa.Numeric(precision=4, scale=1), nullable=True),
    )
    # Server defaults exist only to backfill existing rows; the model itself
    # applies the Python-side default for new rows, so drop them afterwards
    # to keep the schema's source of truth in one place (matches the
    # expense_category precedent in 6da37ec6ea5e).
    op.alter_column("aircraft_booking_settings", "rate_basis", server_default=None)
    op.alter_column("aircraft_booking_settings", "rate_type", server_default=None)


def downgrade() -> None:
    op.drop_column("aircraft_booking_settings", "min_hours_per_day")
    op.drop_column("aircraft_booking_settings", "rate_type")
    op.drop_column("aircraft_booking_settings", "rate_basis")
