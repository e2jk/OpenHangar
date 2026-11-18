"""Explicit group membership and per-edition visit/history consent."""

from alembic import op
import sqlalchemy as sa

revision = "2530f183a938"
down_revision = "0c730082b7e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("passport_series", "passports"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column(
                    "is_group", sa.Boolean(), nullable=False, server_default=sa.false()
                )
            )
    with op.batch_alter_table("passport_participants") as batch:
        batch.add_column(
            sa.Column(
                "share_visits", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.add_column(
            sa.Column(
                "share_history", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
    op.create_table(
        "passport_series_members",
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("series_id", "user_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "series_id"],
            ["passport_series.tenant_id", "passport_series.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["tenant_users.user_id", "tenant_users.tenant_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_passport_series_members_tenant_id", "passport_series_members", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_table("passport_series_members")
    with op.batch_alter_table("passport_participants") as batch:
        batch.drop_column("share_history")
        batch.drop_column("share_visits")
    for table in ("passports", "passport_series"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("is_group")
