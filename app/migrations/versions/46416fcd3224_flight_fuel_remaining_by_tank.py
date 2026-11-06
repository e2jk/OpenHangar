"""Flight: fuel remaining per tank (snapshot list, for multi-tank aircraft)

Revision ID: 46416fcd3224
Revises: 0ea47a2f50fe
Create Date: 2026-11-06 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "46416fcd3224"
down_revision = "0ea47a2f50fe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("flights", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("fuel_remaining_by_tank", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("flights", schema=None) as batch_op:
        batch_op.drop_column("fuel_remaining_by_tank")
