"""Explicit display ordering for undated passport editions."""

from alembic import op
import sqlalchemy as sa

revision = "1adfff5c6e08"
down_revision = "898a3093f9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("passports") as batch:
        batch.add_column(
            sa.Column(
                "series_display_order", sa.Integer(), nullable=False, server_default="0"
            )
        )
        batch.create_check_constraint(
            "ck_passports_edition_order", "series_display_order >= 0"
        )


def downgrade() -> None:
    with op.batch_alter_table("passports") as batch:
        batch.drop_constraint("ck_passports_edition_order", type_="check")
        batch.drop_column("series_display_order")
