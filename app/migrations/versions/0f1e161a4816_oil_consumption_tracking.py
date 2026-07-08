"""Oil consumption tracking

oil_added_l on flight_entries (litres of oil topped up around a flight) and
oil_warning_lph on aircraft (L/h threshold for the cost dashboard warning).

Revision ID: 0f1e161a4816
Revises: d569347024c6
Create Date: 2026-07-09 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0f1e161a4816"
down_revision = "d569347024c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "flight_entries", sa.Column("oil_added_l", sa.Numeric(4, 2), nullable=True)
    )
    op.add_column(
        "aircraft", sa.Column("oil_warning_lph", sa.Numeric(4, 2), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("aircraft", "oil_warning_lph")
    op.drop_column("flight_entries", "oil_added_l")
