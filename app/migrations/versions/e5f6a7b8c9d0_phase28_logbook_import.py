"""Phase 28 — pilot logbook import

Adds logbook_import_mappings, logbook_import_batches tables and extends
pilot_logbook_entries with source + import_batch_id columns.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-05-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "logbook_import_mappings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "pilot_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_fingerprint", sa.String(64), nullable=False, index=True),
        sa.Column("column_mapping", sa.Text, nullable=False),
        sa.Column("source_columns", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "logbook_import_batches",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "pilot_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "mapping_id",
            sa.Integer,
            sa.ForeignKey("logbook_import_mappings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_filename", sa.String(256), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("subtotal_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("has_opening_balance", sa.Boolean, nullable=False, server_default="0"),
    )
    op.add_column(
        "pilot_logbook_entries",
        sa.Column("source", sa.String(32), nullable=True),
    )
    op.add_column(
        "pilot_logbook_entries",
        sa.Column(
            "import_batch_id",
            sa.Integer,
            sa.ForeignKey("logbook_import_batches.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("pilot_logbook_entries", "import_batch_id")
    op.drop_column("pilot_logbook_entries", "source")
    op.drop_table("logbook_import_batches")
    op.drop_table("logbook_import_mappings")
