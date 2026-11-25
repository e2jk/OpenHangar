"""Index the foreign keys that pages filter or cascade on

PostgreSQL does not index foreign keys by itself. The fleet maintenance
page looked up each item's service records by trigger_id (one sequential
scan of maintenance_records per item), and deleting a GPS track, a booking
or an import batch scanned all flights to clear the link.

Revision ID: b804b539f046
Revises: 7850fb45ef70
Create Date: 2026-11-26 00:00:00.000000
"""

from alembic import op

revision = "b804b539f046"
down_revision = "7850fb45ef70"
branch_labels = None
depends_on = None

_INDEXES = (
    ("maintenance_records", "trigger_id"),
    ("flights", "gps_track_id"),
    ("flights", "reservation_id"),
    ("flights", "import_batch_id"),
    ("flights", "gps_import_batch_id"),
    ("flights", "airframe_import_batch_id"),
    ("reservations", "pilot_user_id"),
    ("place_visits", "flight_id"),
)


def upgrade() -> None:
    for table, column in _INDEXES:
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table, column in _INDEXES:
        op.drop_index(f"ix_{table}_{column}", table_name=table)
