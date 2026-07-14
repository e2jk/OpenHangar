"""Phase 37d: reservation-flight link + dispatch records

Revision ID: b3bbd015c4be
Revises: 1a180c372224
Create Date: 2026-07-14 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "b3bbd015c4be"
down_revision = "1a180c372224"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "flight_entries",
        sa.Column("reservation_id", sa.Integer(), nullable=True),
    )
    with op.batch_alter_table("flight_entries") as batch_op:
        batch_op.create_foreign_key(
            "fk_flight_entries_reservation_id",
            "reservations",
            ["reservation_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "dispatch_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reservation_id", sa.Integer(), nullable=False),
        sa.Column("out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("out_by_id", sa.Integer(), nullable=True),
        sa.Column("out_engine_counter", sa.Numeric(precision=8, scale=1), nullable=True),
        sa.Column("out_flight_counter", sa.Numeric(precision=8, scale=1), nullable=True),
        sa.Column("out_fuel_state", sa.String(length=64), nullable=True),
        sa.Column("out_walkaround_ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "out_snags_acknowledged",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "out_grounded_override",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("in_by_id", sa.Integer(), nullable=True),
        sa.Column("in_engine_counter", sa.Numeric(precision=8, scale=1), nullable=True),
        sa.Column("in_flight_counter", sa.Numeric(precision=8, scale=1), nullable=True),
        sa.Column("in_fuel_state", sa.String(length=64), nullable=True),
        sa.Column("in_notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["reservation_id"], ["reservations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["out_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["in_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reservation_id", name="uq_dispatch_records_reservation_id"),
    )
    with op.batch_alter_table("dispatch_records") as batch_op:
        batch_op.alter_column("out_walkaround_ok", server_default=None)
        batch_op.alter_column("out_snags_acknowledged", server_default=None)
        batch_op.alter_column("out_grounded_override", server_default=None)


def downgrade() -> None:
    op.drop_table("dispatch_records")
    with op.batch_alter_table("flight_entries") as batch_op:
        batch_op.drop_constraint(
            "fk_flight_entries_reservation_id", type_="foreignkey"
        )
        batch_op.drop_column("reservation_id")
