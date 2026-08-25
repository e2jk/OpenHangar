"""Phase 40: MaintenanceImportBatch + MaintenanceTrigger.import_batch_id

Revision ID: 9e2b8243ce12
Revises: f2f3ba0a30dd
Create Date: 2026-08-26 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "9e2b8243ce12"
down_revision = "f2f3ba0a30dd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maintenance_import_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("aircraft_id", sa.Integer(), nullable=False),
        sa.Column("source_filename", sa.String(length=256), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("needs_review_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["aircraft_id"], ["aircraft.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_maintenance_import_batches_aircraft_id",
        "maintenance_import_batches",
        ["aircraft_id"],
    )
    op.add_column(
        "maintenance_triggers",
        sa.Column("import_batch_id", sa.Integer(), nullable=True),
    )
    with op.batch_alter_table("maintenance_triggers") as batch_op:
        batch_op.create_foreign_key(
            "fk_maintenance_triggers_import_batch_id",
            "maintenance_import_batches",
            ["import_batch_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_maintenance_triggers_import_batch_id", ["import_batch_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("maintenance_triggers") as batch_op:
        batch_op.drop_index("ix_maintenance_triggers_import_batch_id")
        batch_op.drop_constraint(
            "fk_maintenance_triggers_import_batch_id", type_="foreignkey"
        )
    op.drop_column("maintenance_triggers", "import_batch_id")
    op.drop_index(
        "ix_maintenance_import_batches_aircraft_id",
        table_name="maintenance_import_batches",
    )
    op.drop_table("maintenance_import_batches")
