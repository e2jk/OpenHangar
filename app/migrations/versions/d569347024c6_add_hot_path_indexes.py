"""Add indexes on hot foreign-key and date columns

PostgreSQL does not auto-index FK columns; per-aircraft flight lists, pilot
logbook pages, expense lists, and cascade deletes were seq-scanning their
tables. Composite indexes match the standard list orderings.

Revision ID: d569347024c6
Revises: 6da37ec6ea5e
Create Date: 2026-07-09 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "d569347024c6"
down_revision = "6da37ec6ea5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_flight_entries_aircraft_id_date_id",
        "flight_entries",
        ["aircraft_id", sa.literal_column("date DESC"), sa.literal_column("id DESC")],
    )
    op.create_index(
        "ix_pilot_logbook_entries_pilot_user_id_date_id",
        "pilot_logbook_entries",
        [
            "pilot_user_id",
            sa.literal_column("date DESC"),
            sa.literal_column("id DESC"),
        ],
    )
    op.create_index("ix_expenses_aircraft_id", "expenses", ["aircraft_id"])
    op.create_index("ix_documents_aircraft_id", "documents", ["aircraft_id"])
    op.create_index("ix_documents_pilot_user_id", "documents", ["pilot_user_id"])
    op.create_index("ix_documents_component_id", "documents", ["component_id"])
    op.create_index("ix_documents_flight_entry_id", "documents", ["flight_entry_id"])
    op.create_index("ix_reservations_aircraft_id", "reservations", ["aircraft_id"])
    op.create_index(
        "ix_maintenance_triggers_aircraft_id", "maintenance_triggers", ["aircraft_id"]
    )
    op.create_index("ix_flight_crew_flight_id", "flight_crew", ["flight_id"])


def downgrade() -> None:
    op.drop_index("ix_flight_crew_flight_id", table_name="flight_crew")
    op.drop_index(
        "ix_maintenance_triggers_aircraft_id", table_name="maintenance_triggers"
    )
    op.drop_index("ix_reservations_aircraft_id", table_name="reservations")
    op.drop_index("ix_documents_flight_entry_id", table_name="documents")
    op.drop_index("ix_documents_component_id", table_name="documents")
    op.drop_index("ix_documents_pilot_user_id", table_name="documents")
    op.drop_index("ix_documents_aircraft_id", table_name="documents")
    op.drop_index("ix_expenses_aircraft_id", table_name="expenses")
    op.drop_index(
        "ix_pilot_logbook_entries_pilot_user_id_date_id",
        table_name="pilot_logbook_entries",
    )
    op.drop_index(
        "ix_flight_entries_aircraft_id_date_id", table_name="flight_entries"
    )
