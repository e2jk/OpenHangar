"""Phase 34: Email Notifications

Revision ID: 6b784bed5c65
Revises: 4fb93e0a71d2
Create Date: 2026-06-11 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "6b784bed5c65"
down_revision = "4fb93e0a71d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_profiles",
        sa.Column("email_subject_prefix", sa.String(length=64), nullable=True),
    )

    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("notification_type", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("threshold_days", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "tenant_id", "notification_type", name="uq_notif_pref"
        ),
    )

    op.create_table(
        "tenant_notification_defaults",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("notification_type", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("threshold_days", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "notification_type", name="uq_tenant_notif_default"
        ),
    )


def downgrade() -> None:
    op.drop_table("tenant_notification_defaults")
    op.drop_table("notification_preferences")
    op.drop_column("tenant_profiles", "email_subject_prefix")
