"""Add takeoff_time/landing_time to flights

Tracks the actual airborne segment (wheels-up/wheels-down) separately from
the existing departure_time/arrival_time pair, which double as the
engine-hours window (block-off/block-on, engine assumed on at block-off).
Both new columns are optional and never derived from the block times.

Revision ID: 7e90492c58df
Revises: f5263faf12a4
Create Date: 2026-07-28 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "7e90492c58df"
down_revision = "f5263faf12a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("flights", sa.Column("takeoff_time", sa.Time(), nullable=True))
    op.add_column("flights", sa.Column("landing_time", sa.Time(), nullable=True))


def downgrade() -> None:
    op.drop_column("flights", "landing_time")
    op.drop_column("flights", "takeoff_time")
