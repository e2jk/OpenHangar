"""Add the aircraft's usual refuel timing

refuel_timing says whether this aircraft is usually refuelled before or
after a flight ("before" | "after"): the airframe logbook import uses it for
a fuel quantity with no before/after note, and the flight form lists that
side first. NULL (every existing aircraft) keeps the previous behaviour:
after for the import, before then after on the form.

Revision ID: b6a9bf51fd7a
Revises: 9236ba9be744
Create Date: 2026-11-23 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "b6a9bf51fd7a"
down_revision = "9236ba9be744"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "aircraft",
        sa.Column("refuel_timing", sa.String(8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("aircraft", "refuel_timing")
