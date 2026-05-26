"""GPS import: linked flight tracking and pilot role

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-05-26 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c5d6e7f8a9"
down_revision: str = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Track which FlightEntry IDs were linked (not created) by a GPS import batch.
    # Used by rollback to null-out the GPS track instead of deleting the entry.
    op.add_column(
        "aircraft_gps_import_batches",
        sa.Column(
            "linked_flight_entry_ids",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )
    # Store the pilot role chosen during GPS import ('pic', 'dual', 'none').
    op.add_column(
        "aircraft_gps_import_batches",
        sa.Column("pilot_role", sa.String(8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("aircraft_gps_import_batches", "pilot_role")
    op.drop_column("aircraft_gps_import_batches", "linked_flight_entry_ids")
