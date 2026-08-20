"""Notification send log

Revision ID: 54c5d99c7285
Revises: bcfc743c7626
Create Date: 2026-08-20 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "54c5d99c7285"
down_revision = "bcfc743c7626"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_send_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("notification_type", sa.String(length=64), nullable=False),
        sa.Column("subject_ref", sa.String(length=128), nullable=False),
        sa.Column("sent_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "tenant_id",
            "notification_type",
            "subject_ref",
            "sent_date",
            name="uq_notif_send_log",
        ),
    )


def downgrade() -> None:
    op.drop_table("notification_send_log")
