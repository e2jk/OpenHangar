"""GPS track: link to the kept original GPS file

Revision ID: 71caf9252536
Revises: 46416fcd3224
Create Date: 2026-11-07 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "71caf9252536"
down_revision = "46416fcd3224"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("gps_tracks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("source_file", sa.String(length=512), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("gps_tracks", schema=None) as batch_op:
        batch_op.drop_column("source_file")
