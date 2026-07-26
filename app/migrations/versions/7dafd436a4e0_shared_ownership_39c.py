"""Phase 39c: co-owner overdue threshold

Adds TenantProfile.co_owner_overdue_days (days a co-owner capital balance
may stay negative before the billing dashboard flags it), defaulting to
30, mirroring the existing rental_authorization_policy/
grounded_reservation_policy per-tenant settings.

Revision ID: 7dafd436a4e0
Revises: 7c49f032d65b
Create Date: 2026-07-26 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "7dafd436a4e0"
down_revision = "7c49f032d65b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_profiles",
        sa.Column(
            "co_owner_overdue_days",
            sa.Integer(),
            nullable=False,
            server_default="30",
        ),
    )
    with op.batch_alter_table("tenant_profiles") as batch_op:
        batch_op.alter_column("co_owner_overdue_days", server_default=None)


def downgrade() -> None:
    op.drop_column("tenant_profiles", "co_owner_overdue_days")
