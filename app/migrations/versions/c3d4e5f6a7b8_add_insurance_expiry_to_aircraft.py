"""add insurance_expiry to aircraft

Revision ID: c3d4e5f6a7b8
Revises: b7e3d1f09c22
Create Date: 2026-05-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b7e3d1f09c22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("aircraft", sa.Column("insurance_expiry", sa.Date, nullable=True))


def downgrade() -> None:
    op.drop_column("aircraft", "insurance_expiry")
