"""Expense: optional amount paid in another currency (informative only)

Revision ID: 0ea47a2f50fe
Revises: b1b3643367a7
Create Date: 2026-11-06 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0ea47a2f50fe"
down_revision = "b1b3643367a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("expenses", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("paid_amount", sa.Numeric(precision=10, scale=2), nullable=True)
        )
        batch_op.add_column(
            sa.Column("paid_currency", sa.String(length=4), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("expenses", schema=None) as batch_op:
        batch_op.drop_column("paid_currency")
        batch_op.drop_column("paid_amount")
