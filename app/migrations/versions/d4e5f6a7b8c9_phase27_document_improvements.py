"""Phase 27 — document improvements

Adds pilot_user_id, doc_type, valid_until, superseded_by_id to documents
and makes aircraft_id nullable to support pilot-profile documents.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("documents", "aircraft_id", nullable=True)
    op.add_column(
        "documents",
        sa.Column(
            "pilot_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "documents", sa.Column("doc_type", sa.String(32), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("valid_until", sa.Date, nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column(
            "superseded_by_id",
            sa.Integer,
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "superseded_by_id")
    op.drop_column("documents", "valid_until")
    op.drop_column("documents", "doc_type")
    op.drop_column("documents", "pilot_user_id")
    op.alter_column("documents", "aircraft_id", nullable=False)
