"""Reverse a ledger entry at most once.

Revision ID: febde4fa83c5
Revises: 1bd08c3d5206
"""

from alembic import op

revision = "febde4fa83c5"
down_revision = "1bd08c3d5206"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_ledger_entries_reverses_id", "ledger_entries", ["reverses_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_ledger_entries_reverses_id", table_name="ledger_entries")
