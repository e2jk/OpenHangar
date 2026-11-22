"""Dismissed "record a visit?" suggestions after a flight (Phase 42e).

Revision ID: 9236ba9be744
Revises: 9e08d7fb0714
"""

import sqlalchemy as sa
from alembic import op

revision = "9236ba9be744"
down_revision = "9e08d7fb0714"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "place_visit_dismissals",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("flight_id", sa.Integer(), nullable=False),
        sa.Column("place_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["flight_id"], ["flights.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["place_id"], ["places.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "flight_id", "place_id"),
    )


def downgrade() -> None:
    op.drop_table("place_visit_dismissals")
