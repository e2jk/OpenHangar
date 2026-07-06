"""Phase 36: aircraft operating cost dashboard

Revision ID: 6da37ec6ea5e
Revises: 4acb7648a089
Create Date: 2026-07-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6da37ec6ea5e"
down_revision: str | None = "4acb7648a089"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "expenses",
        sa.Column(
            "expense_category",
            sa.String(16),
            nullable=False,
            server_default="operating",
        ),
    )
    op.add_column("expenses", sa.Column("coverage_start", sa.Date, nullable=True))
    op.add_column("expenses", sa.Column("coverage_end", sa.Date, nullable=True))
    op.execute(
        "UPDATE expenses SET expense_category = 'fixed' WHERE expense_type = 'insurance'"
    )
    op.alter_column("expenses", "expense_category", server_default=None)

    op.add_column(
        "aircraft", sa.Column("reserve_hourly_rate", sa.Numeric(8, 2), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("aircraft", "reserve_hourly_rate")
    op.drop_column("expenses", "coverage_end")
    op.drop_column("expenses", "coverage_start")
    op.drop_column("expenses", "expense_category")
