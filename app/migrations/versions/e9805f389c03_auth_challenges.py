"""Store expiring authentication challenges on the server.

Revision ID: e9805f389c03
Revises: fe82c97a3441
"""

from alembic import op
import sqlalchemy as sa

revision = "e9805f389c03"
down_revision = "fe82c97a3441"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_challenges",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("credential_binding", sa.String(64)),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_auth_challenges_expires_at", "auth_challenges", ["expires_at"])


def downgrade() -> None:
    op.drop_table("auth_challenges")
