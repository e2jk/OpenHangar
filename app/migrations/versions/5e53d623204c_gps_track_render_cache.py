"""GPS track render cache for single-flight PNG/GIF export

Adds cached_png and cached_gif to gps_tracks so the default (landscape,
low-res) single-flight image/GIF renders once and is served from the DB
on every subsequent request — geojson never changes once saved, so no
invalidation is ever needed.

Revision ID: 5e53d623204c
Revises: 84bcacd2d42c
Create Date: 2026-07-13 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "5e53d623204c"
down_revision = "84bcacd2d42c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gps_tracks",
        sa.Column("cached_png", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "gps_tracks",
        sa.Column("cached_gif", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("gps_tracks", "cached_gif")
    op.drop_column("gps_tracks", "cached_png")
