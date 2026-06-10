"""Phase 33: Airworthiness Requirements Tracker

Revision ID: 4fb93e0a71d2
Revises: b8c9d0e1f2a3
Create Date: 2026-06-10 00:00:00.000000
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "4fb93e0a71d2"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "easa_source_nodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("component_id", sa.Integer(), nullable=False),
        sa.Column("tc_holder_node_id", sa.String(length=16), nullable=False),
        sa.Column("tc_holder_name", sa.String(length=128), nullable=False),
        sa.Column("type_node_id", sa.String(length=16), nullable=False),
        sa.Column("type_name", sa.String(length=128), nullable=False),
        sa.Column("model_node_id", sa.String(length=16), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["component_id"], ["components.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "airworthiness_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("doc_type", sa.String(length=16), nullable=False),
        sa.Column("reference", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column("source_node_id", sa.Integer(), nullable=True),
        sa.Column("component_id", sa.Integer(), nullable=True),
        sa.Column("doc_url", sa.String(length=512), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["source_node_id"], ["easa_source_nodes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["component_id"], ["components.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "airworthiness_document_statuses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("aircraft_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending_review"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("compliance_date", sa.Date(), nullable=True),
        sa.Column("next_review_date", sa.Date(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["aircraft_id"], ["aircraft.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_id"], ["airworthiness_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("aircraft_id", "document_id", name="uq_aw_status"),
    )

    op.create_table(
        "installed_stcs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("aircraft_id", sa.Integer(), nullable=False),
        sa.Column("stc_number", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column("tc_holder", sa.String(length=128), nullable=True),
        sa.Column("installation_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["aircraft_id"], ["aircraft.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("installed_stcs")
    op.drop_table("airworthiness_document_statuses")
    op.drop_table("airworthiness_documents")
    op.drop_table("easa_source_nodes")
