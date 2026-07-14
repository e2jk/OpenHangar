"""Phase 37f: availability guards — grounded reservation policy + maintenance downtimes

Revision ID: 7fa37f9e0121
Revises: 9ae07018c7cc
Create Date: 2026-07-14 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "7fa37f9e0121"
down_revision = "9ae07018c7cc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_profiles",
        sa.Column(
            "grounded_reservation_policy",
            sa.String(length=8),
            nullable=False,
            server_default="warn",
        ),
    )
    with op.batch_alter_table("tenant_profiles") as batch_op:
        batch_op.alter_column("grounded_reservation_policy", server_default=None)

    op.create_table(
        "maintenance_downtimes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("aircraft_id", sa.Integer(), nullable=False),
        sa.Column("start_dt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_dt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["aircraft_id"], ["aircraft.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("maintenance_downtimes")
    with op.batch_alter_table("tenant_profiles") as batch_op:
        batch_op.drop_column("grounded_reservation_policy")
