"""Phase 37a: shared billing core (BillingAccount, LedgerEntry)

Revision ID: 722deeb814e0
Revises: 5e53d623204c
Create Date: 2026-07-14 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "722deeb814e0"
down_revision = "5e53d623204c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "billing_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("aircraft_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("currency", sa.String(length=4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["aircraft_id"], ["aircraft.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # A plain UniqueConstraint on (tenant_id, user_id, kind, aircraft_id) would
    # NOT prevent duplicate renter/member accounts: aircraft_id is NULL for
    # those tenant-scoped kinds, and unique constraints treat NULL as distinct
    # from every other NULL. Two partial unique indexes close that gap.
    op.create_index(
        "uq_billing_account_scope_aircraft",
        "billing_accounts",
        ["tenant_id", "user_id", "kind", "aircraft_id"],
        unique=True,
        sqlite_where=sa.text("aircraft_id IS NOT NULL"),
        postgresql_where=sa.text("aircraft_id IS NOT NULL"),
    )
    op.create_index(
        "uq_billing_account_scope_fleet",
        "billing_accounts",
        ["tenant_id", "user_id", "kind"],
        unique=True,
        sqlite_where=sa.text("aircraft_id IS NULL"),
        postgresql_where=sa.text("aircraft_id IS NULL"),
    )
    op.create_index(
        "ix_billing_accounts_tenant_id", "billing_accounts", ["tenant_id"]
    )

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("entry_type", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("reverses_id", sa.Integer(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["billing_accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["reverses_id"], ["ledger_entries.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ledger_entries_account_id", "ledger_entries", ["account_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_ledger_entries_account_id", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index("ix_billing_accounts_tenant_id", table_name="billing_accounts")
    op.drop_index(
        "uq_billing_account_scope_fleet", table_name="billing_accounts"
    )
    op.drop_index(
        "uq_billing_account_scope_aircraft", table_name="billing_accounts"
    )
    op.drop_table("billing_accounts")
