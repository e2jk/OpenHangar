"""Revoke login cookies after logout and account security changes.

Revision ID: d9cb18db220f
Revises: e9805f389c03
"""

from alembic import op
import sqlalchemy as sa

revision = "d9cb18db220f"
down_revision = "e9805f389c03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("auth_version", sa.String(32), nullable=False, server_default=""))
    op.alter_column("users", "auth_version", server_default=None)
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE")),
        sa.Column("credential_binding", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("auth_sessions")
    op.drop_column("users", "auth_version")
