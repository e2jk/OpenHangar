"""Drop unused Aircraft columns: is_placeholder and regime

Both columns were never used to drive any feature logic and are removed
to keep the schema clean.

Revision ID: c2d3e4f5a6b7
Revises: a8f3c1e9d2b7
Create Date: 2026-06-03

"""
from alembic import op
import sqlalchemy as sa

revision = "c2d3e4f5a6b7"
down_revision = "a8f3c1e9d2b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("aircraft", "is_placeholder")
    op.drop_column("aircraft", "regime")


def downgrade() -> None:
    op.add_column("aircraft", sa.Column("regime", sa.String(length=8), nullable=False, server_default="EASA"))
    op.add_column("aircraft", sa.Column("is_placeholder", sa.Boolean(), nullable=False, server_default=sa.false()))
