"""Stamp photos a participant explicitly shows to their edition's group.

Revision ID: 599f6d97deef
Revises: 573cebd8fee2
"""

import sqlalchemy as sa
from alembic import op

revision = "599f6d97deef"
down_revision = "573cebd8fee2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("passport_stamp_photos") as batch:
        batch.add_column(
            sa.Column("group_shared_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    # Dropping the column only makes shared photos private again.
    with op.batch_alter_table("passport_stamp_photos") as batch:
        batch.drop_column("group_shared_at")
