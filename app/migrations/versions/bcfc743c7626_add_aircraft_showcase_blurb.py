"""Backlog: public showcase page per aircraft — showcase_blurb column

Revision ID: bcfc743c7626
Revises: 962109a45d5a
Create Date: 2026-08-18 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "bcfc743c7626"
down_revision = "962109a45d5a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("aircraft", sa.Column("showcase_blurb", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("aircraft", "showcase_blurb")
