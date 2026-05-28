"""Add device_id to gps_tracks for avionics device recognition

Revision ID: d5e6f7a8b9c0
Revises: c6d7e8f9a0b1
Create Date: 2026-05-28 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "d5e6f7a8b9c0"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gps_tracks", sa.Column("device_id", sa.String(64), nullable=True))
    op.create_index("ix_gps_tracks_device_id", "gps_tracks", ["device_id"])


def downgrade() -> None:
    op.drop_index("ix_gps_tracks_device_id", table_name="gps_tracks")
    op.drop_column("gps_tracks", "device_id")
