"""AMP: AmpRevision content_hash + pdf_path (draft detection, canonical PDF)

Lets the export route tell whether the AMP's live data still matches the
last declared revision (content_hash, set when the revision is added) and
reuse a cached canonical PDF instead of regenerating it (pdf_path, set the
first time a download happens while unchanged).

Revision ID: aaea863d1ccb
Revises: f98e00a07cc7
Create Date: 2026-08-28 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "aaea863d1ccb"
down_revision = "f98e00a07cc7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "amp_revisions", sa.Column("content_hash", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "amp_revisions", sa.Column("pdf_path", sa.String(length=512), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("amp_revisions", "pdf_path")
    op.drop_column("amp_revisions", "content_hash")
