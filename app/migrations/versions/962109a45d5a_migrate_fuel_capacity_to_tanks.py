"""Backlog: fold Aircraft.fuel_capacity_liters into AircraftFuelTank

Completes the per-tank fuel capacity work (f65702182cf9): that migration
added AircraftFuelTank purely additively, alongside the pre-existing
fuel_capacity_liters scalar. This migration finishes the "replace the
scalar column" part of the original backlog item — every aircraft that
had a combined capacity set gets a single "Main" tank carrying that same
figure, then the now-redundant column is dropped. Aircraft that already
had their own AircraftFuelTank rows (added directly through the new UI
before this migration ran) are left untouched, so a real capacity never
gets silently duplicated into a second "Main" tank.

Downgrade is lossy the same way eade17c1c735 (fuel_added before/after
split) documents: multiple independently-named tanks have no single
scalar to unambiguously collapse back into, so the downgrade sums each
aircraft's tank capacities into fuel_capacity_liters — a reasonable
"combined total" reading of the old field, not a byte-for-byte revert.
The AircraftFuelTank rows themselves are left in place either way; only
f65702182cf9's downgrade removes the table.

Revision ID: 962109a45d5a
Revises: f65702182cf9
Create Date: 2026-08-18 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "962109a45d5a"
down_revision = "f65702182cf9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    aircraft = sa.table(
        "aircraft",
        sa.column("id", sa.Integer),
        sa.column("fuel_capacity_liters", sa.Numeric),
    )
    tanks = sa.table(
        "aircraft_fuel_tanks",
        sa.column("id", sa.Integer),
        sa.column("aircraft_id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("capacity_liters", sa.Numeric),
        sa.column("sort_order", sa.Integer),
    )

    conn = op.get_bind()
    already_has_tank = {
        row[0] for row in conn.execute(sa.select(tanks.c.aircraft_id).distinct())
    }
    rows = conn.execute(
        sa.select(aircraft.c.id, aircraft.c.fuel_capacity_liters).where(
            aircraft.c.fuel_capacity_liters.isnot(None)
        )
    )
    for aircraft_id, capacity in rows:
        if aircraft_id in already_has_tank:
            continue
        conn.execute(
            tanks.insert().values(
                aircraft_id=aircraft_id,
                name="Main",
                capacity_liters=capacity,
                sort_order=0,
            )
        )

    op.drop_column("aircraft", "fuel_capacity_liters")


def downgrade() -> None:
    op.add_column(
        "aircraft",
        sa.Column(
            "fuel_capacity_liters", sa.Numeric(precision=6, scale=1), nullable=True
        ),
    )

    aircraft = sa.table(
        "aircraft",
        sa.column("id", sa.Integer),
        sa.column("fuel_capacity_liters", sa.Numeric),
    )
    tanks = sa.table(
        "aircraft_fuel_tanks",
        sa.column("id", sa.Integer),
        sa.column("aircraft_id", sa.Integer),
        sa.column("capacity_liters", sa.Numeric),
    )

    conn = op.get_bind()
    totals = conn.execute(
        sa.select(tanks.c.aircraft_id, sa.func.sum(tanks.c.capacity_liters)).group_by(
            tanks.c.aircraft_id
        )
    )
    for aircraft_id, total_capacity in totals:
        conn.execute(
            aircraft.update()
            .where(aircraft.c.id == aircraft_id)
            .values(fuel_capacity_liters=total_capacity)
        )
