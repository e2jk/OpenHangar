"""Phase 40: MaintenanceTrigger AMP import/export fields

Adds optional component scoping plus AMP import/export provenance and
classification fields to maintenance_triggers: component_id, category,
is_alternative_to_ica, alternative_task_notes, reference, action,
part_number, serial_number, needs_review.

Revision ID: 2b4794041a59
Revises: e9db32d7b279
Create Date: 2026-08-26 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "2b4794041a59"
down_revision = "e9db32d7b279"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maintenance_triggers",
        sa.Column("component_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "maintenance_triggers",
        sa.Column("category", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "maintenance_triggers",
        sa.Column(
            "is_alternative_to_ica",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "maintenance_triggers",
        sa.Column("alternative_task_notes", sa.Text(), nullable=True),
    )
    op.add_column(
        "maintenance_triggers",
        sa.Column("reference", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "maintenance_triggers", sa.Column("action", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "maintenance_triggers",
        sa.Column("part_number", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "maintenance_triggers",
        sa.Column("serial_number", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "maintenance_triggers",
        sa.Column(
            "needs_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    with op.batch_alter_table("maintenance_triggers") as batch_op:
        batch_op.create_foreign_key(
            "fk_maintenance_triggers_component_id",
            "components",
            ["component_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_maintenance_triggers_component_id", ["component_id"])
        batch_op.alter_column("is_alternative_to_ica", server_default=None)
        batch_op.alter_column("needs_review", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("maintenance_triggers") as batch_op:
        batch_op.drop_index("ix_maintenance_triggers_component_id")
        batch_op.drop_constraint(
            "fk_maintenance_triggers_component_id", type_="foreignkey"
        )
    op.drop_column("maintenance_triggers", "needs_review")
    op.drop_column("maintenance_triggers", "serial_number")
    op.drop_column("maintenance_triggers", "part_number")
    op.drop_column("maintenance_triggers", "action")
    op.drop_column("maintenance_triggers", "reference")
    op.drop_column("maintenance_triggers", "alternative_task_notes")
    op.drop_column("maintenance_triggers", "is_alternative_to_ica")
    op.drop_column("maintenance_triggers", "category")
    op.drop_column("maintenance_triggers", "component_id")
