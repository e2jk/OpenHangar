"""Independent destination collections, membership and retained visit history.

Revision ID: eddedce0eaf4
Revises: f81dbff14bed
"""

import sqlalchemy as sa
from alembic import op

revision = "eddedce0eaf4"
down_revision = "f81dbff14bed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "place_collections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "is_personal", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id"),
    )
    op.create_index(
        op.f("ix_place_collections_tenant_id"),
        "place_collections",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "uq_place_personal_collection",
        "place_collections",
        ["tenant_id", "created_by_user_id"],
        unique=True,
        postgresql_where=sa.text("is_personal"),
        sqlite_where=sa.text("is_personal = 1"),
    )
    op.create_table(
        "place_collection_members",
        sa.Column("collection_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("can_edit", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "collection_id"],
            ["place_collections.tenant_id", "place_collections.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["tenant_users.user_id", "tenant_users.tenant_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("collection_id", "user_id"),
    )
    op.create_index(
        op.f("ix_place_collection_members_tenant_id"),
        "place_collection_members",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "place_preferences",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("default_collection_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["default_collection_id"], ["place_collections.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["tenant_users.user_id", "tenant_users.tenant_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "tenant_id"),
    )
    op.create_table(
        "place_tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("collection_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.ForeignKeyConstraint(
            ["collection_id"], ["place_collections.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("collection_id", "id"),
        sa.UniqueConstraint("collection_id", "name"),
    )
    op.create_index(
        op.f("ix_place_tags_collection_id"),
        "place_tags",
        ["collection_id"],
        unique=False,
    )
    op.create_table(
        "places",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("collection_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("external_url", sa.String(length=2048), nullable=True),
        sa.Column("photo_filename", sa.String(length=128), nullable=True),
        sa.Column("archived", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_place_latitude"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_place_longitude"),
        sa.ForeignKeyConstraint(
            ["collection_id"], ["place_collections.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("collection_id", "id"),
        sa.UniqueConstraint("photo_filename"),
    )
    op.create_index(
        op.f("ix_places_collection_id"), "places", ["collection_id"], unique=False
    )
    op.create_index(
        op.f("ix_places_deleted_at"), "places", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_places_owner_user_id"), "places", ["owner_user_id"], unique=False
    )
    op.create_table(
        "place_tag_assignments",
        sa.Column("place_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.Column("collection_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["collection_id", "place_id"],
            ["places.collection_id", "places.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["collection_id", "tag_id"],
            ["place_tags.collection_id", "place_tags.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("place_id", "tag_id"),
    )
    op.create_table(
        "place_visits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("place_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("visit_date", sa.Date(), nullable=False),
        sa.Column("flight_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["flight_id"], ["flights.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["place_id"], ["places.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_place_visits_place_id"), "place_visits", ["place_id"], unique=False
    )
    op.create_index(
        op.f("ix_place_visits_user_id"), "place_visits", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_place_visits_user_id"), table_name="place_visits")
    op.drop_index(op.f("ix_place_visits_place_id"), table_name="place_visits")
    op.drop_table("place_visits")
    op.drop_table("place_tag_assignments")
    op.drop_index(op.f("ix_places_owner_user_id"), table_name="places")
    op.drop_index(op.f("ix_places_deleted_at"), table_name="places")
    op.drop_index(op.f("ix_places_collection_id"), table_name="places")
    op.drop_table("places")
    op.drop_index(op.f("ix_place_tags_collection_id"), table_name="place_tags")
    op.drop_table("place_tags")
    op.drop_table("place_preferences")
    op.drop_index(
        op.f("ix_place_collection_members_tenant_id"),
        table_name="place_collection_members",
    )
    op.drop_table("place_collection_members")
    op.drop_index(
        "uq_place_personal_collection",
        table_name="place_collections",
        postgresql_where=sa.text("is_personal"),
        sqlite_where=sa.text("is_personal = 1"),
    )
    op.drop_index(
        op.f("ix_place_collections_tenant_id"), table_name="place_collections"
    )
    op.drop_table("place_collections")
