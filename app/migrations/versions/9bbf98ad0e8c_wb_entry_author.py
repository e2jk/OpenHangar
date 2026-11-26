"""Record who made each mass & balance calculation

Any pilot of an aircraft could edit or delete every calculation on it. The
author is now recorded, and only they (or an owner or admin) change it.
Existing calculations have no recorded author and stay as they were.

Revision ID: 9bbf98ad0e8c
Revises: b804b539f046
Create Date: 2026-11-26 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "9bbf98ad0e8c"
down_revision = "b804b539f046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("wb_entries") as batch:
        batch.add_column(sa.Column("created_by_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_wb_entries_created_by_user_id_users",
            "users",
            ["created_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("wb_entries") as batch:
        batch.drop_constraint("fk_wb_entries_created_by_user_id_users", type_="foreignkey")
        batch.drop_column("created_by_user_id")
