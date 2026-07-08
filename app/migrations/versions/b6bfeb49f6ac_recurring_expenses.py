"""Recurring fixed-cost expenses

recurrence (monthly/quarterly/yearly), recurrence_end, the materialiser
cursor recurrence_last_date, and the self-referencing recurring_template_id
linking generated rows back to their template expense.

Revision ID: b6bfeb49f6ac
Revises: f1e1c2a73ab2
Create Date: 2026-07-09 01:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "b6bfeb49f6ac"
down_revision = "f1e1c2a73ab2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("expenses", sa.Column("recurrence", sa.String(16), nullable=True))
    op.add_column("expenses", sa.Column("recurrence_end", sa.Date(), nullable=True))
    op.add_column(
        "expenses", sa.Column("recurrence_last_date", sa.Date(), nullable=True)
    )
    op.add_column(
        "expenses", sa.Column("recurring_template_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_expenses_recurring_template_id",
        "expenses",
        "expenses",
        ["recurring_template_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_expenses_recurring_template_id", "expenses", type_="foreignkey"
    )
    op.drop_column("expenses", "recurring_template_id")
    op.drop_column("expenses", "recurrence_last_date")
    op.drop_column("expenses", "recurrence_end")
    op.drop_column("expenses", "recurrence")
