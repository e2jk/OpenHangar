"""Passport-scoped invitations and persistent group/edition removal."""

from alembic import op
import sqlalchemy as sa

revision = "371231c6fbd0"
down_revision = "8a8b38500aef"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("passports") as batch:
        batch.create_unique_constraint("uq_passports_series_id", ["series_id", "id"])
    with op.batch_alter_table("passport_participants") as batch:
        batch.add_column(
            sa.Column(
                "blocked", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
    op.create_table(
        "passport_invitations",
        sa.Column("passport_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("key_nonce", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("passport_id"),
        sa.UniqueConstraint("token_hash"),
        sa.ForeignKeyConstraint(["passport_id"], ["passports.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "passport_group_blocks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("passport_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("email_hash", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["series_id"], ["passport_series.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["series_id", "passport_id"],
            ["passports.series_id", "passports.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("series_id", "passport_id", "email_hash"),
    )
    op.create_index(
        "ix_passport_group_blocks_series_id", "passport_group_blocks", ["series_id"]
    )
    op.create_index(
        "ix_passport_group_blocks_user_id", "passport_group_blocks", ["user_id"]
    )
    op.create_index(
        "uq_passport_group_block",
        "passport_group_blocks",
        ["series_id", "email_hash"],
        unique=True,
        postgresql_where=sa.text("passport_id IS NULL"),
        sqlite_where=sa.text("passport_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("passport_group_blocks")
    op.drop_table("passport_invitations")
    with op.batch_alter_table("passport_participants") as batch:
        batch.drop_column("blocked")
    with op.batch_alter_table("passports") as batch:
        batch.drop_constraint("uq_passports_series_id", type_="unique")
