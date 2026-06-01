"""Add require_totp flag to tenants

Revision ID: 1b2c3d4e5f6a
Revises: f6a7b8c9d0e1, 6cdc6bca98f6
Create Date: 2026-06-01 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision: str = "1b2c3d4e5f6a"
down_revision = ("f6a7b8c9d0e1", "6cdc6bca98f6")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "require_totp",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "require_totp")
