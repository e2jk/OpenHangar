"""Phase 39a: shared ownership — AircraftOwner model

Adds the aircraft_owners table (one row per co-owner, share_pct + buy_in
amount) and two nullable Aircraft columns (co_owner_hourly_rate,
co_owner_billing_start) that stay null until the manage-owners form is
first saved.

Revision ID: 7c49f032d65b
Revises: 517626af44f3
Create Date: 2026-07-26 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "7c49f032d65b"
down_revision = "517626af44f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "aircraft",
        sa.Column("co_owner_hourly_rate", sa.Numeric(8, 2), nullable=True),
    )
    op.add_column(
        "aircraft",
        sa.Column("co_owner_billing_start", sa.Date(), nullable=True),
    )

    op.create_table(
        "aircraft_owners",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("aircraft_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("share_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column(
            "buy_in_amount", sa.Numeric(10, 2), nullable=False, server_default="0"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["aircraft_id"], ["aircraft.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("aircraft_id", "user_id", name="uq_aircraft_owner"),
    )
    with op.batch_alter_table("aircraft_owners") as batch_op:
        batch_op.alter_column("buy_in_amount", server_default=None)
    op.create_index(
        "ix_aircraft_owners_aircraft_id", "aircraft_owners", ["aircraft_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_aircraft_owners_aircraft_id", table_name="aircraft_owners")
    op.drop_table("aircraft_owners")
    op.drop_column("aircraft", "co_owner_billing_start")
    op.drop_column("aircraft", "co_owner_hourly_rate")
