"""Notification snoozes

Revision ID: e904c15b49df
Revises: 54c5d99c7285
Create Date: 2026-08-20 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "e904c15b49df"
down_revision = "54c5d99c7285"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_snoozes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("notification_type", sa.String(length=64), nullable=False),
        sa.Column("subject_ref", sa.String(length=128), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=False),
        sa.Column("current_value", sa.String(length=64), nullable=False),
        sa.Column("snoozed_value", sa.String(length=64), nullable=True),
        sa.Column("snoozed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_notif_snooze_token"),
        sa.UniqueConstraint(
            "user_id",
            "tenant_id",
            "notification_type",
            "subject_ref",
            name="uq_notif_snooze",
        ),
    )


def downgrade() -> None:
    op.drop_table("notification_snoozes")
