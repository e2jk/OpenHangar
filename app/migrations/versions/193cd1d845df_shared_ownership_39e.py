"""Phase 39e: co-owner valuation snapshots

Adds co_owner_valuation_snapshots — an immutable point-in-time capital
value per co-owner (no update/delete route ever exists for this table).

Revision ID: 193cd1d845df
Revises: 7dafd436a4e0
Create Date: 2026-07-26 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "193cd1d845df"
down_revision = "7dafd436a4e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "co_owner_valuation_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("aircraft_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("valuation_date", sa.Date(), nullable=False),
        sa.Column("share_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("capital_balance", sa.Numeric(10, 2), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["aircraft_id"], ["aircraft.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_covs_aircraft_id", "co_owner_valuation_snapshots", ["aircraft_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_covs_aircraft_id", table_name="co_owner_valuation_snapshots"
    )
    op.drop_table("co_owner_valuation_snapshots")
