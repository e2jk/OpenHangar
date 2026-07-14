"""Phase 37e: rental charges + expense creator tracking

Revision ID: 9ae07018c7cc
Revises: b3bbd015c4be
Create Date: 2026-07-14 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "9ae07018c7cc"
down_revision = "b3bbd015c4be"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "expenses",
        sa.Column("created_by_id", sa.Integer(), nullable=True),
    )
    with op.batch_alter_table("expenses") as batch_op:
        batch_op.create_foreign_key(
            "fk_expenses_created_by_id", "users", ["created_by_id"], ["id"], ondelete="SET NULL"
        )

    op.create_table(
        "rental_charges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reservation_id", sa.Integer(), nullable=False),
        sa.Column("renter_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=12), nullable=False, server_default="draft"
        ),
        sa.Column("billable_hours", sa.Numeric(precision=6, scale=1), nullable=False),
        sa.Column("hourly_rate", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("rate_type", sa.String(length=8), nullable=False),
        sa.Column(
            "fuel_credit",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "adjustment",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
            server_default="0",
        ),
        sa.Column("adjustment_note", sa.String(length=255), nullable=True),
        sa.Column(
            "fallback_counter_used",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("total", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["reservation_id"], ["reservations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["renter_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["finalized_by_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reservation_id", name="uq_rental_charges_reservation_id"),
    )
    with op.batch_alter_table("rental_charges") as batch_op:
        batch_op.alter_column("status", server_default=None)
        batch_op.alter_column("fuel_credit", server_default=None)
        batch_op.alter_column("adjustment", server_default=None)
        batch_op.alter_column("fallback_counter_used", server_default=None)


def downgrade() -> None:
    op.drop_table("rental_charges")
    with op.batch_alter_table("expenses") as batch_op:
        batch_op.drop_constraint("fk_expenses_created_by_id", type_="foreignkey")
        batch_op.drop_column("created_by_id")
