"""Keep the hourly rate each co-owner flight charge was billed at

Every co-owner billing pass re-priced all past flight charges (and hourly
reserve contributions) at the aircraft's current rate, so raising the rate
re-billed flights already settled. Charges now record their rate
(ledger_entries.unit_rate) and keep it.

Since every past pass re-priced to the current rate, the live charges are
all at it today: they are backfilled with it.

Revision ID: 7850fb45ef70
Revises: c6ab03391954
Create Date: 2026-11-25 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "7850fb45ef70"
down_revision = "c6ab03391954"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ledger_entries", sa.Column("unit_rate", sa.Numeric(10, 2), nullable=True)
    )
    op.execute(
        "UPDATE ledger_entries SET unit_rate = ("
        "SELECT aircraft.co_owner_hourly_rate FROM billing_accounts "
        "JOIN aircraft ON aircraft.id = billing_accounts.aircraft_id "
        "WHERE billing_accounts.id = ledger_entries.account_id) "
        "WHERE source_type = 'flight_usage' AND reverses_id IS NULL"
    )
    op.execute(
        "UPDATE ledger_entries SET unit_rate = ("
        "SELECT aircraft.reserve_contribution_hourly FROM billing_accounts "
        "JOIN aircraft ON aircraft.id = billing_accounts.aircraft_id "
        "WHERE billing_accounts.id = ledger_entries.account_id) "
        "WHERE source_type = 'reserve_contribution' AND reverses_id IS NULL "
    )


def downgrade() -> None:
    op.drop_column("ledger_entries", "unit_rate")
