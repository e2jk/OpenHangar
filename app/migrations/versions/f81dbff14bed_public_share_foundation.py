"""Generic public capabilities and separate Internet publication consent.

Revision ID: f81dbff14bed
Revises: 5c3f59cd82cb
"""

import sqlalchemy as sa
from alembic import op

revision = "f81dbff14bed"
down_revision = "5c3f59cd82cb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "public_shares",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "issuer_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("resource_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("key_nonce", sa.String(43), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "resource_type", "resource_id"),
    )
    with op.batch_alter_table("passport_participants") as batch:
        batch.add_column(sa.Column("public_shared_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("public_frequency_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("public_name", sa.String(128)))
    with op.batch_alter_table("passport_stamp_photos") as batch:
        batch.add_column(sa.Column("public_shared_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    # A downgrade removes public capabilities and makes publication unavailable.
    op.drop_table("public_shares")
    with op.batch_alter_table("passport_stamp_photos") as batch:
        batch.drop_column("public_shared_at")
    with op.batch_alter_table("passport_participants") as batch:
        batch.drop_column("public_name")
        batch.drop_column("public_frequency_at")
        batch.drop_column("public_shared_at")
