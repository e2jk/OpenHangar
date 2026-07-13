"""Pilot logbook: FSTD / simulator session entries

Adds entry_type (LogbookEntryType constant, default "flight"), fstd_type
(FstdType constant), and fstd_duration to pilot_logbook_entries so EASA
AMC1 FCL.050 column 10 sessions can be logged alongside flight entries.

Revision ID: 84bcacd2d42c
Revises: e0a4cf6ba84e
Create Date: 2026-07-13 09:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "84bcacd2d42c"
down_revision = "e0a4cf6ba84e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pilot_logbook_entries",
        sa.Column("entry_type", sa.String(16), nullable=False, server_default="flight"),
    )
    op.add_column(
        "pilot_logbook_entries",
        sa.Column("fstd_type", sa.String(16), nullable=True),
    )
    op.add_column(
        "pilot_logbook_entries",
        sa.Column("fstd_duration", sa.Numeric(4, 1), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pilot_logbook_entries", "fstd_duration")
    op.drop_column("pilot_logbook_entries", "fstd_type")
    op.drop_column("pilot_logbook_entries", "entry_type")
