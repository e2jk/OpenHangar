"""Phase 31b: standalone GpsTrack model

Revision ID: c6d7e8f9a0b1
Revises: c5d6e7f8a9b0
Create Date: 2026-05-27 00:00:00.000000

"""

import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "c6d7e8f9a0b1"
down_revision: str = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gps_tracks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_filename", sa.String(256), nullable=True),
        sa.Column("block_off_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("block_on_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("departure_icao", sa.String(4), nullable=True),
        sa.Column("arrival_icao", sa.String(4), nullable=True),
        sa.Column("geojson", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column(
        "flight_entries",
        sa.Column("gps_track_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_flight_entries_gps_track_id",
        "flight_entries",
        "gps_tracks",
        ["gps_track_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Migrate existing track_geojson data into the new gps_tracks table.
    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        sa.text(
            "SELECT id, track_geojson, block_off_utc, block_on_utc, "
            "departure_icao, arrival_icao "
            "FROM flight_entries WHERE track_geojson IS NOT NULL"
        )
    ).fetchall()
    for row in rows:
        geojson_val = row[1]
        # In PostgreSQL, JSON columns are returned as dicts; serialise for insert.
        if isinstance(geojson_val, (dict, list)):
            geojson_val = json.dumps(geojson_val)
        result = conn.execute(
            sa.text(
                "INSERT INTO gps_tracks "
                "(source_filename, block_off_utc, block_on_utc, "
                "departure_icao, arrival_icao, geojson, created_at) "
                "VALUES (NULL, :boff, :bon, :dep, :arr, :geojson, :now) "
                "RETURNING id"
            ),
            {
                "boff": row[2],
                "bon": row[3],
                "dep": row[4],
                "arr": row[5],
                "geojson": geojson_val,
                "now": now,
            },
        )
        track_id = result.fetchone()[0]
        conn.execute(
            sa.text(
                "UPDATE flight_entries SET gps_track_id = :tid WHERE id = :fid"
            ),
            {"tid": track_id, "fid": row[0]},
        )

    op.drop_column("flight_entries", "track_geojson")

    op.add_column(
        "pilot_logbook_entries",
        sa.Column("gps_track_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_pilot_logbook_entries_gps_track_id",
        "pilot_logbook_entries",
        "gps_tracks",
        ["gps_track_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_pilot_logbook_entries_gps_track_id",
        "pilot_logbook_entries",
        type_="foreignkey",
    )
    op.drop_column("pilot_logbook_entries", "gps_track_id")

    op.add_column(
        "flight_entries",
        sa.Column("track_geojson", sa.JSON(), nullable=True),
    )
    # Restore geojson data from gps_tracks back to flight_entries
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT fe.id, gt.geojson FROM flight_entries fe "
            "JOIN gps_tracks gt ON gt.id = fe.gps_track_id"
        )
    ).fetchall()
    for row in rows:
        geojson_val = row[1]
        if isinstance(geojson_val, (dict, list)):
            geojson_val = json.dumps(geojson_val)
        conn.execute(
            sa.text(
                "UPDATE flight_entries SET track_geojson = :gj WHERE id = :fid"
            ),
            {"gj": geojson_val, "fid": row[0]},
        )

    op.drop_constraint(
        "fk_flight_entries_gps_track_id",
        "flight_entries",
        type_="foreignkey",
    )
    op.drop_column("flight_entries", "gps_track_id")
    op.drop_table("gps_tracks")
