"""Backlog: per-tank fuel capacity tracking

Revision ID: f65702182cf9
Revises: 8735be6c8de5
Create Date: 2026-08-18 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "f65702182cf9"
down_revision = "8735be6c8de5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "aircraft_fuel_tanks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("aircraft_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("capacity_liters", sa.Numeric(precision=6, scale=1), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["aircraft_id"], ["aircraft.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("aircraft_fuel_tanks") as batch_op:
        batch_op.alter_column("sort_order", server_default=None)
    op.create_index(
        "ix_aircraft_fuel_tanks_aircraft_id", "aircraft_fuel_tanks", ["aircraft_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_aircraft_fuel_tanks_aircraft_id", table_name="aircraft_fuel_tanks"
    )
    op.drop_table("aircraft_fuel_tanks")
