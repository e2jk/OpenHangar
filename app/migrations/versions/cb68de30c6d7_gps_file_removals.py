"""GPS file removals: a trash for GPS files taken off flights

Revision ID: cb68de30c6d7
Revises: 35706aeaa7dc
Create Date: 2026-11-08 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "cb68de30c6d7"
down_revision = "35706aeaa7dc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gps_file_removals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("flight_id", sa.Integer(), nullable=True),
        sa.Column("flight_label", sa.String(length=96), nullable=True),
        sa.Column("removed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=16), nullable=False),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("part", sa.JSON(), nullable=True),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["flight_id"], ["flights.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["removed_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("gps_file_removals", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_gps_file_removals_flight_id"), ["flight_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("gps_file_removals", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_gps_file_removals_flight_id"))
    op.drop_table("gps_file_removals")
