"""Re-render cached track images: new base map

The cached single-flight PNG/GIF renders used CARTO's base map, which now
only returns "API key required" tiles; cleared so they're drawn again with
the toned-down OpenStreetMap base map (utils._tone_down).

Revision ID: 0b95dd5c5a42
Revises: cb68de30c6d7
Create Date: 2026-11-08 00:00:00.000000
"""

from alembic import op

revision = "0b95dd5c5a42"
down_revision = "cb68de30c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE gps_tracks SET cached_png = NULL, cached_gif = NULL")


def downgrade() -> None:
    pass  # a cache: nothing to restore, it's rebuilt on the next request
