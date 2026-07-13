"""Phase 37c: renter authorization

Revision ID: 1a180c372224
Revises: dbb4562f6703
Create Date: 2026-07-14 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "1a180c372224"
down_revision = "dbb4562f6703"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_profiles",
        sa.Column(
            "rental_authorization_policy",
            sa.String(length=8),
            nullable=False,
            server_default="warn",
        ),
    )
    with op.batch_alter_table("tenant_profiles") as batch_op:
        batch_op.alter_column("rental_authorization_policy", server_default=None)

    op.create_table(
        "renter_authorizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("renter_user_id", sa.Integer(), nullable=False),
        sa.Column("aircraft_id", sa.Integer(), nullable=True),
        sa.Column("authorized_by_id", sa.Integer(), nullable=True),
        sa.Column("granted_on", sa.Date(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("checkout_flight_on", sa.Date(), nullable=True),
        sa.Column("licence_seen_on", sa.Date(), nullable=True),
        sa.Column("medical_valid_until", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["renter_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["aircraft_id"], ["aircraft.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["authorized_by_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_renter_authorizations_tenant_id", "renter_authorizations", ["tenant_id"]
    )
    op.create_index(
        "ix_renter_authorizations_renter_user_id",
        "renter_authorizations",
        ["renter_user_id"],
    )

    op.add_column(
        "documents",
        sa.Column("renter_authorization_id", sa.Integer(), nullable=True),
    )
    with op.batch_alter_table("documents") as batch_op:
        batch_op.create_foreign_key(
            "fk_documents_renter_authorization_id",
            "renter_authorizations",
            ["renter_authorization_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint(
            "fk_documents_renter_authorization_id", type_="foreignkey"
        )
        batch_op.drop_column("renter_authorization_id")

    op.drop_index(
        "ix_renter_authorizations_renter_user_id",
        table_name="renter_authorizations",
    )
    op.drop_index(
        "ix_renter_authorizations_tenant_id", table_name="renter_authorizations"
    )
    op.drop_table("renter_authorizations")

    op.drop_column("tenant_profiles", "rental_authorization_policy")
