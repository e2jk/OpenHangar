"""Attach a receipt/invoice document to an expense

expense_id FK on documents; receipt documents also carry aircraft_id so
existing access control, serving, and the aircraft document list apply.

Revision ID: 1fa0a08914b7
Revises: b6bfeb49f6ac
Create Date: 2026-07-09 01:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "1fa0a08914b7"
down_revision = "b6bfeb49f6ac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("expense_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_documents_expense_id",
        "documents",
        "expenses",
        ["expense_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_documents_expense_id", "documents", ["expense_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_expense_id", table_name="documents")
    op.drop_constraint("fk_documents_expense_id", "documents", type_="foreignkey")
    op.drop_column("documents", "expense_id")
