"""Personal minimums — versioned per-pilot minimums document

Revision ID: 8a3da16e7596
Revises: 7fa37f9e0121
Create Date: 2026-07-14 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "8a3da16e7596"
down_revision = "7fa37f9e0121"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "personal_minimums_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="draft"
        ),
        sa.Column("published_on", sa.Date(), nullable=True),
        sa.Column("experience_hours", sa.Numeric(precision=6, scale=1), nullable=True),
        sa.Column("experience_note", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "revision_number", name="uq_personal_minimums_revision"
        ),
    )
    with op.batch_alter_table("personal_minimums_revisions") as batch_op:
        batch_op.alter_column("status", server_default=None)

    op.create_table(
        "personal_minimums_sections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("revision_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["revision_id"], ["personal_minimums_revisions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("personal_minimums_sections") as batch_op:
        batch_op.alter_column("sort_order", server_default=None)

    op.create_table(
        "personal_minimums_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("semantic_tag", sa.String(length=64), nullable=True),
        sa.Column("numeric_value", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["section_id"], ["personal_minimums_sections.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("personal_minimums_items") as batch_op:
        batch_op.alter_column("sort_order", server_default=None)


def downgrade() -> None:
    op.drop_table("personal_minimums_items")
    op.drop_table("personal_minimums_sections")
    op.drop_table("personal_minimums_revisions")
