"""Add pre-flight counter photo columns to flights

Pre-flight counterparts of the existing flight_counter_photo/
engine_counter_photo columns — taken before departure (e.g. at
block-off) rather than after shutdown. Independent of the post-flight
photos: a flight can have neither, either, or both.

Revision ID: 5e0d72145245
Revises: aaea863d1ccb
Create Date: 2026-09-05 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "5e0d72145245"
down_revision = "aaea863d1ccb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "flights",
        sa.Column("flight_counter_photo_preflight", sa.String(255), nullable=True),
    )
    op.add_column(
        "flights",
        sa.Column("engine_counter_photo_preflight", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("flights", "engine_counter_photo_preflight")
    op.drop_column("flights", "flight_counter_photo_preflight")
