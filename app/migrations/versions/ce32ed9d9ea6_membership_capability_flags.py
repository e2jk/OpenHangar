"""Capability flags per hangar membership

is_pilot, is_maintenance and view_only were columns of the user account,
so any hangar the account belonged to (a passport group's hangar included)
could change them, and they applied in every hangar. They now live on each
membership (tenant_users); every existing membership starts with the
account's current values, so nothing changes for anyone today.

The users columns are kept, no longer read: dropping them is left to a
later migration.

Revision ID: ce32ed9d9ea6
Revises: b6a9bf51fd7a
Create Date: 2026-11-25 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "ce32ed9d9ea6"
down_revision = "b6a9bf51fd7a"
branch_labels = None
depends_on = None

_FLAGS = ("is_pilot", "is_maintenance", "view_only")


def upgrade() -> None:
    for flag in _FLAGS:
        op.add_column(
            "tenant_users",
            sa.Column(flag, sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    op.execute(
        "UPDATE tenant_users SET "
        "is_pilot = (SELECT users.is_pilot FROM users "
        "WHERE users.id = tenant_users.user_id), "
        "is_maintenance = (SELECT users.is_maintenance FROM users "
        "WHERE users.id = tenant_users.user_id), "
        "view_only = (SELECT users.view_only FROM users "
        "WHERE users.id = tenant_users.user_id)"
    )


def downgrade() -> None:
    for flag in _FLAGS:
        op.drop_column("tenant_users", flag)
