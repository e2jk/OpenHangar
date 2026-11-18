"""Explicit, expiring group ownership acceptance.

Revision ID: 4e90f737358d
Revises: 371231c6fbd0
"""

import sqlalchemy as sa
from alembic import op

revision = "4e90f737358d"
down_revision = "371231c6fbd0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "passport_ownership_offers",
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("requester_user_id", sa.Integer(), nullable=False),
        sa.Column("target_user_id", sa.Integer(), nullable=False),
        sa.Column("request_key", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("series_id"),
        sa.ForeignKeyConstraint(
            ["series_id"], ["passport_series.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["requester_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_passport_ownership_offers_target_user_id",
        "passport_ownership_offers",
        ["target_user_id"],
    )


def downgrade() -> None:
    op.drop_table("passport_ownership_offers")
