"""Add avionics_units and equipment_wishlist_items tables (backlog:
avionics/equipment inventory with per-unit status and an upgrade wish list)

Revision ID: 78ec0560a69a
Revises: cbe55d419eb4
Create Date: 2026-09-05 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "78ec0560a69a"
down_revision = "cbe55d419eb4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "avionics_units",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("aircraft_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("make", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("serial_number", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="serviceable",
        ),
        sa.Column("certification_notes", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["aircraft_id"], ["aircraft.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_avionics_units_aircraft_id", "avionics_units", ["aircraft_id"])

    op.create_table(
        "equipment_wishlist_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("aircraft_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("rough_cost", sa.Numeric(10, 2), nullable=True),
        sa.Column("requirements", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["aircraft_id"], ["aircraft.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_equipment_wishlist_items_aircraft_id",
        "equipment_wishlist_items",
        ["aircraft_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_equipment_wishlist_items_aircraft_id",
        table_name="equipment_wishlist_items",
    )
    op.drop_table("equipment_wishlist_items")
    op.drop_index("ix_avionics_units_aircraft_id", table_name="avionics_units")
    op.drop_table("avionics_units")
