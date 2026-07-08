"""Archive/retire an aircraft without deleting its history

archived_at on aircraft: null = active; set = hidden from active-fleet
views, reservations, and notification passes, with all history kept.

Revision ID: f1e1c2a73ab2
Revises: 0f1e161a4816
Create Date: 2026-07-09 00:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "f1e1c2a73ab2"
down_revision = "0f1e161a4816"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "aircraft", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("aircraft", "archived_at")
