"""Blind exact-email collection invitations.

Revision ID: 38faafd5e24b
Revises: eddedce0eaf4
"""

import sqlalchemy as sa
from alembic import op

revision = "38faafd5e24b"
down_revision = "eddedce0eaf4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "place_collection_invitations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("collection_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("email_digest", sa.String(64), nullable=False),
        sa.Column("can_edit", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("collection_id", "email_digest"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "collection_id"],
            ["place_collections.tenant_id", "place_collections.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_place_collection_invitations_collection_id",
        "place_collection_invitations",
        ["collection_id"],
    )
    op.create_index(
        "ix_place_collection_invitations_tenant_id",
        "place_collection_invitations",
        ["tenant_id"],
    )
    op.create_index(
        "ix_place_collection_invitations_expires_at",
        "place_collection_invitations",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_table("place_collection_invitations")
