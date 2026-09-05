"""Add ad_sb_items table (backlog: AD/SB compliance board)

Revision ID: cbe55d419eb4
Revises: 5e0d72145245
Create Date: 2026-09-05 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "cbe55d419eb4"
down_revision = "5e0d72145245"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ad_sb_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("aircraft_id", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="conditional",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["aircraft_id"], ["aircraft.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ad_sb_items_aircraft_id", "ad_sb_items", ["aircraft_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_ad_sb_items_aircraft_id", table_name="ad_sb_items")
    op.drop_table("ad_sb_items")
