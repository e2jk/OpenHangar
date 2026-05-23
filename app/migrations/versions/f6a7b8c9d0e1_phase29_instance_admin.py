"""Phase 29: instance admin, tenant is_active, password reset tokens

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-05-23 00:00:00.000000

"""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: str = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── tenants: add is_active ────────────────────────────────────────────────
    op.add_column(
        "tenants",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )

    # ── users: add is_instance_admin ─────────────────────────────────────────
    op.add_column(
        "users",
        sa.Column(
            "is_instance_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # Promote the oldest admin user on existing installs.
    # The join finds the user who is OWNER/ADMIN of any tenant and was
    # created earliest.  If no such user exists we fall back to the oldest
    # user overall.  Either way exactly one user gets the flag.
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            """
            SELECT u.id
            FROM users u
            JOIN tenant_users tu ON tu.user_id = u.id
            WHERE tu.role IN ('ADMIN', 'OWNER')
              AND u.is_active = TRUE
            ORDER BY u.created_at ASC
            LIMIT 1
            """
        )
    ).fetchone()

    if result is None:
        # Fallback: oldest user regardless of role
        result = conn.execute(
            sa.text("SELECT id FROM users ORDER BY created_at ASC LIMIT 1")
        ).fetchone()

    if result is not None:
        conn.execute(
            sa.text("UPDATE users SET is_instance_admin = TRUE WHERE id = :uid"),
            {"uid": result[0]},
        )

    # ── password_reset_tokens table ───────────────────────────────────────────
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(36), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "generated_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
    )


def downgrade() -> None:
    op.drop_table("password_reset_tokens")
    op.drop_column("users", "is_instance_admin")
    op.drop_column("tenants", "is_active")
