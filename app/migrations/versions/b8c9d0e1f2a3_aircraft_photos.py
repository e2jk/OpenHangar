"""Add aircraft_photos table

Revision ID: b8c9d0e1f2a3
Revises: aa1b2c3d4e5f
Create Date: 2026-06-04 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str = "aa1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "aircraft_photos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "aircraft_id",
            sa.Integer,
            sa.ForeignKey("aircraft.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("original_filename", sa.String(256), nullable=False),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "uploaded_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_aircraft_photos_aircraft_id", "aircraft_photos", ["aircraft_id"])


def downgrade() -> None:
    op.drop_index("ix_aircraft_photos_aircraft_id", "aircraft_photos")
    op.drop_table("aircraft_photos")
