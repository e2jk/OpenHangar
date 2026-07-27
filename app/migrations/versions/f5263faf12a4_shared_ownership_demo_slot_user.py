"""Track a representative shared-ownership co-owner user on DemoSlot

Adds demo_slots.shared_ownership_user_id — one of the 3 co-owners seeded
on shared_ownership_tenant_id, used to log a demo visitor straight into
that role from the landing page, mirroring sole_pilot_user_id/
sole_operator_user_id rather than looking a user up via the tenant at
login time.

Revision ID: f5263faf12a4
Revises: bf4d67d9f886
Create Date: 2026-07-27 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "f5263faf12a4"
down_revision = "bf4d67d9f886"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "demo_slots",
        sa.Column("shared_ownership_user_id", sa.Integer(), nullable=True),
    )
    with op.batch_alter_table("demo_slots") as batch_op:
        batch_op.create_foreign_key(
            "fk_demo_slots_shared_ownership_user_id",
            "users",
            ["shared_ownership_user_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("demo_slots") as batch_op:
        batch_op.drop_constraint(
            "fk_demo_slots_shared_ownership_user_id", type_="foreignkey"
        )
        batch_op.drop_column("shared_ownership_user_id")
