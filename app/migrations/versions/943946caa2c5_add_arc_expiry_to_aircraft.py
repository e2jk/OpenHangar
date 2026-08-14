"""add arc_expiry to aircraft

Revision ID: 943946caa2c5
Revises: e6a27d2e4325
Create Date: 2026-08-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "943946caa2c5"
down_revision: str | None = "e6a27d2e4325"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("aircraft", sa.Column("arc_expiry", sa.Date, nullable=True))


def downgrade() -> None:
    op.drop_column("aircraft", "arc_expiry")
