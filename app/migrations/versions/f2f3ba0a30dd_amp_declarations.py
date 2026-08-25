"""Phase 40: AmpDeclaration (EASA Form AMP profile), one-to-one with Aircraft

Revision ID: f2f3ba0a30dd
Revises: 2b4794041a59
Create Date: 2026-08-26 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "f2f3ba0a30dd"
down_revision = "2b4794041a59"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "amp_declarations",
        sa.Column("aircraft_id", sa.Integer(), nullable=False),
        sa.Column(
            "basis", sa.String(length=16), nullable=False, server_default="dah_ica"
        ),
        sa.Column("mip_details", sa.Text(), nullable=True),
        sa.Column("dah_ica_airframe_ref", sa.String(length=255), nullable=True),
        sa.Column("dah_ica_engine_ref", sa.String(length=255), nullable=True),
        sa.Column("dah_ica_propeller_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "pilot_owner_maintenance",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("pilot_owner_name", sa.String(length=128), nullable=True),
        sa.Column("pilot_owner_licence_number", sa.String(length=64), nullable=True),
        sa.Column(
            "declaration_type",
            sa.String(length=16),
            nullable=False,
            server_default="owner",
        ),
        sa.Column("camo_cao_approval_reference", sa.String(length=128), nullable=True),
        sa.Column(
            "certifying_party_kind",
            sa.String(length=24),
            nullable=False,
            server_default="owner_lessee_operator",
        ),
        sa.Column("certifying_party_name", sa.String(length=128), nullable=True),
        sa.Column("certifying_party_address", sa.Text(), nullable=True),
        sa.Column("certifying_party_phone", sa.String(length=32), nullable=True),
        sa.Column("certifying_party_email", sa.String(length=128), nullable=True),
        sa.Column("appendix_d_notes", sa.Text(), nullable=True),
        sa.Column("revision_number", sa.String(length=16), nullable=True),
        sa.Column("revision_content", sa.String(length=255), nullable=True),
        sa.Column("revision_date", sa.Date(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["aircraft_id"], ["aircraft.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("aircraft_id"),
    )
    with op.batch_alter_table("amp_declarations") as batch_op:
        batch_op.alter_column("basis", server_default=None)
        batch_op.alter_column("pilot_owner_maintenance", server_default=None)
        batch_op.alter_column("declaration_type", server_default=None)
        batch_op.alter_column("certifying_party_kind", server_default=None)


def downgrade() -> None:
    op.drop_table("amp_declarations")
