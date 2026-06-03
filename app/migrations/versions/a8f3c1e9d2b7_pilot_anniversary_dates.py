"""Add first_solo_date and ppl_issue_date to pilot_profiles

Revision ID: a8f3c1e9d2b7
Revises: 1b2c3d4e5f6a
Create Date: 2026-06-03

"""
from alembic import op
import sqlalchemy as sa

revision = "a8f3c1e9d2b7"
down_revision = "1b2c3d4e5f6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pilot_profiles", sa.Column("first_solo_date", sa.Date(), nullable=True))
    op.add_column("pilot_profiles", sa.Column("ppl_issue_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("pilot_profiles", "ppl_issue_date")
    op.drop_column("pilot_profiles", "first_solo_date")
