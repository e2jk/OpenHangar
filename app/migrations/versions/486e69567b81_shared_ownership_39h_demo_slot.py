"""Phase 39h: track the shared-ownership demo sub-tenant on DemoSlot

Adds demo_slots.shared_ownership_tenant_id — the shared-ownership demo
sub-tenant has 3 co-owner users (not 1), so it's tracked by tenant id
rather than reusing the single-user-id column pattern of
sole_pilot_user_id/sole_operator_user_id.

Revision ID: 486e69567b81
Revises: 193cd1d845df
Create Date: 2026-07-26 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "486e69567b81"
down_revision = "193cd1d845df"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "demo_slots",
        sa.Column("shared_ownership_tenant_id", sa.Integer(), nullable=True),
    )
    with op.batch_alter_table("demo_slots") as batch_op:
        batch_op.create_foreign_key(
            "fk_demo_slots_shared_ownership_tenant_id",
            "tenants",
            ["shared_ownership_tenant_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("demo_slots") as batch_op:
        batch_op.drop_constraint(
            "fk_demo_slots_shared_ownership_tenant_id", type_="foreignkey"
        )
        batch_op.drop_column("shared_ownership_tenant_id")
