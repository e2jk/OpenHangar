"""Data checks: flights.updated_at and data_check_dismissals

Revision ID: 35706aeaa7dc
Revises: f3581a6f0989
Create Date: 2026-11-07 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "35706aeaa7dc"
down_revision = "f3581a6f0989"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("flights", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True)
        )
    op.execute("UPDATE flights SET updated_at = created_at")
    op.create_table(
        "data_check_dismissals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("flight_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=96), nullable=False),
        sa.Column("dismissed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["flight_id"], ["flights.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["dismissed_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("flight_id", "key"),
    )
    with op.batch_alter_table("data_check_dismissals", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_data_check_dismissals_flight_id"), ["flight_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("data_check_dismissals", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_data_check_dismissals_flight_id"))
    op.drop_table("data_check_dismissals")
    with op.batch_alter_table("flights", schema=None) as batch_op:
        batch_op.drop_column("updated_at")
