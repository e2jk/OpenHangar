"""GPS track overview: a light line for maps of many flights

Adds gps_tracks.overview (services.track_overview) and fills it for the
existing tracks; new and changed tracks get theirs when saved.

Revision ID: fe82c97a3441
Revises: 0b95dd5c5a42
Create Date: 2026-11-08 00:00:00.000000
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "fe82c97a3441"
down_revision = "0b95dd5c5a42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from services.track_overview import (  # pyright: ignore[reportMissingImports]
        overview_coords,
    )

    with op.batch_alter_table("gps_tracks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("overview", sa.JSON(), nullable=True))
    tracks = sa.table(
        "gps_tracks",
        sa.column("id", sa.Integer),
        sa.column("geojson", sa.JSON),
        sa.column("overview", sa.JSON),
    )
    conn = op.get_bind()
    for track_id, geojson in conn.execute(sa.select(tracks.c.id, tracks.c.geojson)):
        if isinstance(geojson, str):  # JSON stored as text on some backends
            geojson = json.loads(geojson)
        conn.execute(
            tracks.update()
            .where(tracks.c.id == track_id)
            .values(overview=overview_coords(geojson))
        )


def downgrade() -> None:
    with op.batch_alter_table("gps_tracks", schema=None) as batch_op:
        batch_op.drop_column("overview")
