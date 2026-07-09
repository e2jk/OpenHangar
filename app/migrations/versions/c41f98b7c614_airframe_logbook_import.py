"""Bulk import of historical airframe logbook (CSV/Excel)

airframe_import_mappings (fingerprint-keyed column mapping memory per
tenant), airframe_import_batches (one undoable import per aircraft), and
the airframe_import_batch_id link on flight_entries.

Revision ID: c41f98b7c614
Revises: c39babc5ad70
Create Date: 2026-07-09 05:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "c41f98b7c614"
down_revision = "c39babc5ad70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "airframe_import_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("column_mapping", sa.Text(), nullable=False),
        sa.Column("source_columns", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_airframe_import_mappings_source_fingerprint",
        "airframe_import_mappings",
        ["source_fingerprint"],
    )
    op.create_table(
        "airframe_import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "aircraft_id",
            sa.Integer(),
            sa.ForeignKey("aircraft.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "mapping_id",
            sa.Integer(),
            sa.ForeignKey("airframe_import_mappings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_filename", sa.String(256), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("subtotal_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "has_opening_counters",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "flight_entries",
        sa.Column("airframe_import_batch_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_flight_entries_airframe_import_batch_id",
        "flight_entries",
        "airframe_import_batches",
        ["airframe_import_batch_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_flight_entries_airframe_import_batch_id",
        "flight_entries",
        type_="foreignkey",
    )
    op.drop_column("flight_entries", "airframe_import_batch_id")
    op.drop_table("airframe_import_batches")
    op.drop_index(
        "ix_airframe_import_mappings_source_fingerprint",
        table_name="airframe_import_mappings",
    )
    op.drop_table("airframe_import_mappings")
