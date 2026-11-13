"""Shared login throttling and single-use TOTP time steps.

Revision ID: 1bd08c3d5206
Revises: d9cb18db220f
"""

from alembic import op
import sqlalchemy as sa

revision = "1bd08c3d5206"
down_revision = "d9cb18db220f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_last_step", sa.BigInteger(), nullable=True))
    op.create_table(
        "login_throttles",
        sa.Column("key", sa.String(320), primary_key=True),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("window_expires_at", sa.BigInteger(), nullable=False),
        sa.Column("blocked_until", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_login_throttles_window_expires_at", "login_throttles", ["window_expires_at"]
    )


def downgrade() -> None:
    op.drop_table("login_throttles")
    op.drop_column("users", "totp_last_step")
