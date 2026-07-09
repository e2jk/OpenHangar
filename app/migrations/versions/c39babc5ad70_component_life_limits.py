"""Engine/propeller TBO & life-limited component tracking

First-class life-limit columns on components: tbo_hours (hours between
overhauls), life_limit_date (calendar-limited parts), and the overhaul
reference point (overhauled_at_hours / overhauled_on).  Legacy TBO values
stored in the extras JSON blob are copied into the new column.

Revision ID: c39babc5ad70
Revises: 1fa0a08914b7
Create Date: 2026-07-09 03:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "c39babc5ad70"
down_revision = "1fa0a08914b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("components", sa.Column("tbo_hours", sa.Numeric(8, 1), nullable=True))
    op.add_column("components", sa.Column("life_limit_date", sa.Date(), nullable=True))
    op.add_column(
        "components", sa.Column("overhauled_at_hours", sa.Numeric(8, 1), nullable=True)
    )
    op.add_column("components", sa.Column("overhauled_on", sa.Date(), nullable=True))
    # Copy legacy TBO values kept in the extras JSON blob (Phase 1) into the
    # new column so existing data keeps working.
    op.execute(
        sa.text(
            """
            UPDATE components
            SET tbo_hours = (extras ->> 'tbo_hours')::numeric
            WHERE tbo_hours IS NULL
              AND extras ->> 'tbo_hours' ~ '^[0-9]+\\.?[0-9]*$'
            """
        )
    )


def downgrade() -> None:
    op.drop_column("components", "overhauled_on")
    op.drop_column("components", "overhauled_at_hours")
    op.drop_column("components", "life_limit_date")
    op.drop_column("components", "tbo_hours")
