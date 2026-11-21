"""Group-visible airfield discussions, independent of annual participation.

Revision ID: 573cebd8fee2
Revises: 5abc051fdcae
"""

import sys

import sqlalchemy as sa
from alembic import op

revision = "573cebd8fee2"
down_revision = "5abc051fdcae"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "passport_airfield_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("airfield_id", sa.Integer(), nullable=False),
        sa.Column("author_user_id", sa.Integer(), nullable=False),
        sa.Column("author_display_name", sa.String(128), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("experience_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "series_id"],
            ["passport_series.tenant_id", "passport_series.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "airfield_id"],
            [
                "passport_airfield_identities.tenant_id",
                "passport_airfield_identities.id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint("category IN ('airfield', 'nearby', 'question')"),
        sa.CheckConstraint("length(body) BETWEEN 1 AND 8000"),
    )
    op.create_index(
        "ix_passport_comments_group_field_created",
        "passport_airfield_comments",
        ["series_id", "airfield_id", "created_at", "id"],
    )
    op.create_index(
        "ix_passport_airfield_comments_author_user_id",
        "passport_airfield_comments",
        ["author_user_id"],
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM passport_airfield_comments"))
        .scalar()
    ):
        print(
            "Export and explicitly remove group comments before downgrading.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    op.drop_table("passport_airfield_comments")
