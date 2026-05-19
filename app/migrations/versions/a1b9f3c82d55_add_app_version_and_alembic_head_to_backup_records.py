"""add app_version and alembic_head to backup_records

Revision ID: a1b9f3c82d55
Revises: 3f8a2c91b047
Create Date: 2026-05-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b9f3c82d55"
down_revision: str | None = "3f8a2c91b047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_records") as batch_op:
        batch_op.add_column(sa.Column("app_version", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("alembic_head", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("backup_records") as batch_op:
        batch_op.drop_column("alembic_head")
        batch_op.drop_column("app_version")
