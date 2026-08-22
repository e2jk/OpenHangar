"""Airframe import batch is_historical flag

Scopes leniency for pre-existing paper-logbook imprecision (OCR misreads,
duration mismatches) to a specific import batch, toggleable after the
fact, instead of inferring it from the importer in general.

Revision ID: e9db32d7b279
Revises: 73fa9c13e134
Create Date: 2026-08-21 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "e9db32d7b279"
down_revision = "73fa9c13e134"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "airframe_import_batches",
        sa.Column(
            "is_historical",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("airframe_import_batches", "is_historical")
