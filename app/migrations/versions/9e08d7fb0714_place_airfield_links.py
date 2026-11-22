"""Collection-scoped airfield links, independent of passports and flights.

Revision ID: 9e08d7fb0714
Revises: 8b9733b19ccf
"""

import sqlalchemy as sa
from alembic import op

revision = "9e08d7fb0714"
down_revision = "8b9733b19ccf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "place_airfields",
        sa.Column("place_id", sa.Integer(), primary_key=True),
        sa.Column("airfield_id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("collection_id", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(2000), nullable=True),
        sa.Column("preferred", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "public_catalogue", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "collection_id"],
            ["place_collections.tenant_id", "place_collections.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["collection_id", "place_id"],
            ["places.collection_id", "places.id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "airfield_id"],
            [
                "passport_airfield_identities.tenant_id",
                "passport_airfield_identities.id",
            ],
            ondelete="CASCADE",
        ),
    )
    for column in ("airfield_id", "tenant_id", "collection_id"):
        op.create_index(f"ix_place_airfields_{column}", "place_airfields", [column])


def downgrade() -> None:
    op.drop_table("place_airfields")
