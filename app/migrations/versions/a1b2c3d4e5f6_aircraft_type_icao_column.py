"""Add aircraft_type_icao to pilot_logbook_entries

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-05-24 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pilot_logbook_entries",
        sa.Column("aircraft_type_icao", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pilot_logbook_entries", "aircraft_type_icao")
