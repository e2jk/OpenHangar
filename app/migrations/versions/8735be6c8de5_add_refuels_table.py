"""Backlog: standalone refuel record (not tied to any flight)

Revision ID: 8735be6c8de5
Revises: 943946caa2c5
Create Date: 2026-08-18 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "8735be6c8de5"
down_revision = "943946caa2c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refuels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("aircraft_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("unit", sa.String(length=8), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["aircraft_id"], ["aircraft.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_refuels_aircraft_id", "refuels", ["aircraft_id"])


def downgrade() -> None:
    op.drop_index("ix_refuels_aircraft_id", table_name="refuels")
    op.drop_table("refuels")
