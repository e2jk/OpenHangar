"""Split oil_added_l into independent before/after fields

Mirrors the fuel_added_before/after split (see 1c1b8d93ff90's sibling
migration eade17c1c735): oil can be topped off before departure, after
landing, or both, and the old single oil_added_l scalar could only ever
record one figure. Existing values are backfilled into oil_added_after_l —
topping off oil is more commonly a postflight action (noticed after flying)
than a preflight one, so "after" is the more representative bucket for
data with no recorded before/after distinction at all.

Downgrade sums both columns back into oil_added_l (COALESCE-summed, NULL
only when both are NULL) — unlike the fuel migration this direction isn't
lossy, since liters simply add.

Revision ID: e6a27d2e4325
Revises: eade17c1c735
Create Date: 2026-08-13 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "e6a27d2e4325"
down_revision = "eade17c1c735"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "flights", sa.Column("oil_added_before_l", sa.Numeric(4, 2), nullable=True)
    )
    op.add_column(
        "flights", sa.Column("oil_added_after_l", sa.Numeric(4, 2), nullable=True)
    )

    flights = sa.table(
        "flights",
        sa.column("id", sa.Integer),
        sa.column("oil_added_l", sa.Numeric),
        sa.column("oil_added_after_l", sa.Numeric),
    )
    op.execute(
        flights.update()
        .where(flights.c.oil_added_l.isnot(None))
        .values(oil_added_after_l=flights.c.oil_added_l)
    )

    op.drop_column("flights", "oil_added_l")


def downgrade() -> None:
    op.add_column("flights", sa.Column("oil_added_l", sa.Numeric(4, 2), nullable=True))

    op.execute(
        """
        UPDATE flights
        SET oil_added_l = COALESCE(oil_added_before_l, 0) + COALESCE(oil_added_after_l, 0)
        WHERE oil_added_before_l IS NOT NULL OR oil_added_after_l IS NOT NULL
        """
    )

    op.drop_column("flights", "oil_added_after_l")
    op.drop_column("flights", "oil_added_before_l")
