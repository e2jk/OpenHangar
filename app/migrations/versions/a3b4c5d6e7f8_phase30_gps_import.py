"""Phase 30: aircraft GPS log import

Revision ID: a3b4c5d6e7f8
Revises: f6a7b8c9d0e1
Create Date: 2026-05-23 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3b4c5d6e7f8"
down_revision: str = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── aircraft_gps_import_batches ───────────────────────────────────────────
    op.create_table(
        "aircraft_gps_import_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("aircraft_id", sa.Integer(), nullable=False),
        sa.Column("pilot_user_id", sa.Integer(), nullable=True),
        sa.Column("source_filenames", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("format_detected", sa.String(16), nullable=False, server_default=""),
        sa.Column("segments_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("segments_imported", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["aircraft_id"], ["aircraft.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["pilot_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── flight_entries: GPS import columns ────────────────────────────────────
    op.add_column(
        "flight_entries",
        sa.Column("source", sa.String(32), nullable=True),
    )
    op.add_column(
        "flight_entries",
        sa.Column("gps_import_batch_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "flight_entries",
        sa.Column("block_off_utc", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "flight_entries",
        sa.Column("block_on_utc", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "flight_entries",
        sa.Column("track_geojson", sa.JSON(), nullable=True),
    )
    op.create_foreign_key(
        "fk_flight_entries_gps_import_batch_id",
        "flight_entries",
        "aircraft_gps_import_batches",
        ["gps_import_batch_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ── aircraft: logbook time precision preference ────────────────────────────
    op.add_column(
        "aircraft",
        sa.Column(
            "logbook_time_precision",
            sa.String(16),
            nullable=False,
            server_default="tenth_hour",
        ),
    )

    # ── pilot_logbook_entries: GPS import batch FK ────────────────────────────
    op.add_column(
        "pilot_logbook_entries",
        sa.Column("gps_batch_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_pilot_logbook_entries_gps_batch_id",
        "pilot_logbook_entries",
        "aircraft_gps_import_batches",
        ["gps_batch_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_pilot_logbook_entries_gps_batch_id",
        "pilot_logbook_entries",
        type_="foreignkey",
    )
    op.drop_column("pilot_logbook_entries", "gps_batch_id")

    op.drop_column("aircraft", "logbook_time_precision")

    op.drop_constraint(
        "fk_flight_entries_gps_import_batch_id",
        "flight_entries",
        type_="foreignkey",
    )
    op.drop_column("flight_entries", "track_geojson")
    op.drop_column("flight_entries", "block_on_utc")
    op.drop_column("flight_entries", "block_off_utc")
    op.drop_column("flight_entries", "gps_import_batch_id")
    op.drop_column("flight_entries", "source")

    op.drop_table("aircraft_gps_import_batches")
