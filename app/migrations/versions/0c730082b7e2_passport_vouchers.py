"""Explicit personal voucher allocations, shared pools and redemption history."""

from alembic import op
import sqlalchemy as sa

revision = "0c730082b7e2"
down_revision = "1adfff5c6e08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("passports") as batch:
        batch.add_column(
            sa.Column(
                "default_voucher_quantity",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.create_check_constraint(
            "ck_passports_voucher_default",
            "default_voucher_quantity BETWEEN 0 AND 100000",
        )
    op.create_table(
        "passport_voucher_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "passport_id",
            sa.Integer(),
            sa.ForeignKey("passports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column(
            "personal_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("passport_id", "id"),
        sa.UniqueConstraint("passport_id", "target_id", "personal_user_id"),
        sa.ForeignKeyConstraint(
            ["passport_id", "target_id"],
            ["passport_airfields.passport_id", "passport_airfields.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("quantity BETWEEN 0 AND 100000"),
        sa.CheckConstraint(
            "valid_from IS NULL OR valid_until IS NULL OR valid_from <= valid_until"
        ),
    )
    for column in ("passport_id", "target_id", "personal_user_id"):
        op.create_index(
            f"ix_passport_voucher_allocations_{column}",
            "passport_voucher_allocations",
            [column],
        )
    op.create_index(
        "uq_passport_voucher_shared",
        "passport_voucher_allocations",
        ["passport_id", "target_id"],
        unique=True,
        postgresql_where=sa.text("personal_user_id IS NULL"),
        sqlite_where=sa.text("personal_user_id IS NULL"),
    )
    op.create_table(
        "passport_voucher_redemptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("passport_id", sa.Integer(), nullable=False),
        sa.Column("allocation_id", sa.Integer(), nullable=False),
        sa.Column(
            "participant_id",
            sa.Integer(),
            sa.ForeignKey("passport_participants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("redemption_date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column(
            "flight_id",
            sa.Integer(),
            sa.ForeignKey("flights.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("saving_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("request_key", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "replaces_id",
            sa.Integer(),
            sa.ForeignKey("passport_voucher_redemptions.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("passport_id", "id"),
        sa.ForeignKeyConstraint(
            ["passport_id", "replaces_id"],
            [
                "passport_voucher_redemptions.passport_id",
                "passport_voucher_redemptions.id",
            ],
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["passport_id", "allocation_id"],
            [
                "passport_voucher_allocations.passport_id",
                "passport_voucher_allocations.id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["passport_id", "participant_id"],
            ["passport_participants.passport_id", "passport_participants.id"],
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint("quantity BETWEEN 1 AND 100000"),
        sa.CheckConstraint("saving_amount IS NULL OR saving_amount >= 0"),
        sa.CheckConstraint("(saving_amount IS NULL) = (currency IS NULL)"),
    )
    for column in ("passport_id", "allocation_id", "participant_id", "flight_id"):
        op.create_index(
            f"ix_passport_voucher_redemptions_{column}",
            "passport_voucher_redemptions",
            [column],
        )


def downgrade() -> None:
    op.drop_table("passport_voucher_redemptions")
    op.drop_table("passport_voucher_allocations")
    with op.batch_alter_table("passports") as batch:
        batch.drop_constraint("ck_passports_voucher_default", type_="check")
        batch.drop_column("default_voucher_quantity")
