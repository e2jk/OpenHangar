"""Shared flights: who logged a flight, a personal remark per crew slot, and
correction suggestions (backlog: let a second crew member claim their own
slot on an existing flight — phase 2, per-pilot editing boundary)

Revision ID: 2978bd3abf28
Revises: d6c446f1e8e5
Create Date: 2026-09-13 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "2978bd3abf28"
down_revision = "d6c446f1e8e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("flights", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pic_remarks", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("second_crew_remarks", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("created_by_user_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_flights_created_by_user_id_users",
            "users",
            ["created_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "flight_correction_suggestions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("flight_id", sa.Integer(), nullable=False),
        sa.Column("suggested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["flight_id"], ["flights.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["suggested_by_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_flight_correction_suggestions_flight_id",
        "flight_correction_suggestions",
        ["flight_id"],
    )
    op.create_index(
        "ix_flight_correction_suggestions_suggested_by_user_id",
        "flight_correction_suggestions",
        ["suggested_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_flight_correction_suggestions_suggested_by_user_id",
        table_name="flight_correction_suggestions",
    )
    op.drop_index(
        "ix_flight_correction_suggestions_flight_id",
        table_name="flight_correction_suggestions",
    )
    op.drop_table("flight_correction_suggestions")
    with op.batch_alter_table("flights", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_flights_created_by_user_id_users", type_="foreignkey"
        )
        batch_op.drop_column("created_by_user_id")
        batch_op.drop_column("second_crew_remarks")
        batch_op.drop_column("pic_remarks")
