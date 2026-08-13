"""Split fuel_event/fuel_added_qty into independent before/after fields

A flight can be refueled both before departure and after landing (two
separate top-offs bracketing the same flight) — the old fuel_event
('before' | 'after' | None) plus single fuel_added_qty/unit could only ever
record one of the two. This replaces them with independent
fuel_added_before_qty/unit and fuel_added_after_qty/unit columns, backfilled
from the existing fuel_event value.

Downgrade is lossy when both before and after are set on the same row (the
old schema has no slot for two amounts): it prefers the "after" figure,
falling back to "before" if only that is set — matching the more common
real-world case of noting fuel added after landing.

Revision ID: eade17c1c735
Revises: 1c1b8d93ff90
Create Date: 2026-08-13 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "eade17c1c735"
down_revision = "1c1b8d93ff90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "flights", sa.Column("fuel_added_before_qty", sa.Numeric(8, 2), nullable=True)
    )
    op.add_column(
        "flights", sa.Column("fuel_added_before_unit", sa.String(8), nullable=True)
    )
    op.add_column(
        "flights", sa.Column("fuel_added_after_qty", sa.Numeric(8, 2), nullable=True)
    )
    op.add_column(
        "flights", sa.Column("fuel_added_after_unit", sa.String(8), nullable=True)
    )

    flights = sa.table(
        "flights",
        sa.column("id", sa.Integer),
        sa.column("fuel_event", sa.String),
        sa.column("fuel_added_qty", sa.Numeric),
        sa.column("fuel_added_unit", sa.String),
        sa.column("fuel_added_before_qty", sa.Numeric),
        sa.column("fuel_added_before_unit", sa.String),
        sa.column("fuel_added_after_qty", sa.Numeric),
        sa.column("fuel_added_after_unit", sa.String),
    )
    op.execute(
        flights.update()
        .where(flights.c.fuel_event == "before")
        .values(
            fuel_added_before_qty=flights.c.fuel_added_qty,
            fuel_added_before_unit=flights.c.fuel_added_unit,
        )
    )
    op.execute(
        flights.update()
        .where(flights.c.fuel_event == "after")
        .values(
            fuel_added_after_qty=flights.c.fuel_added_qty,
            fuel_added_after_unit=flights.c.fuel_added_unit,
        )
    )

    op.drop_column("flights", "fuel_event")
    op.drop_column("flights", "fuel_added_qty")
    op.drop_column("flights", "fuel_added_unit")


def downgrade() -> None:
    op.add_column("flights", sa.Column("fuel_event", sa.String(8), nullable=True))
    op.add_column(
        "flights", sa.Column("fuel_added_qty", sa.Numeric(8, 2), nullable=True)
    )
    op.add_column("flights", sa.Column("fuel_added_unit", sa.String(8), nullable=True))

    flights = sa.table(
        "flights",
        sa.column("id", sa.Integer),
        sa.column("fuel_event", sa.String),
        sa.column("fuel_added_qty", sa.Numeric),
        sa.column("fuel_added_unit", sa.String),
        sa.column("fuel_added_before_qty", sa.Numeric),
        sa.column("fuel_added_before_unit", sa.String),
        sa.column("fuel_added_after_qty", sa.Numeric),
        sa.column("fuel_added_after_unit", sa.String),
    )
    op.execute(
        flights.update()
        .where(flights.c.fuel_added_before_qty.isnot(None))
        .values(
            fuel_event="before",
            fuel_added_qty=flights.c.fuel_added_before_qty,
            fuel_added_unit=flights.c.fuel_added_before_unit,
        )
    )
    op.execute(
        flights.update()
        .where(flights.c.fuel_added_after_qty.isnot(None))
        .values(
            fuel_event="after",
            fuel_added_qty=flights.c.fuel_added_after_qty,
            fuel_added_unit=flights.c.fuel_added_after_unit,
        )
    )

    op.drop_column("flights", "fuel_added_after_unit")
    op.drop_column("flights", "fuel_added_after_qty")
    op.drop_column("flights", "fuel_added_before_unit")
    op.drop_column("flights", "fuel_added_before_qty")
