"""Add flight_crew_invites table (backlog: let a second crew member claim
their own slot on an existing flight — phase 1, invite via the crew name
picker and confirm/decline)

Revision ID: d6c446f1e8e5
Revises: 78ec0560a69a
Create Date: 2026-09-13 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "d6c446f1e8e5"
down_revision = "78ec0560a69a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flight_crew_invites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("flight_id", sa.Integer(), nullable=False),
        sa.Column("slot", sa.String(length=16), nullable=False),
        sa.Column("invited_user_id", sa.Integer(), nullable=False),
        sa.Column("invited_by_user_id", sa.Integer(), nullable=True),
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
            ["invited_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_flight_crew_invites_flight_id", "flight_crew_invites", ["flight_id"]
    )
    op.create_index(
        "ix_flight_crew_invites_invited_user_id_status",
        "flight_crew_invites",
        ["invited_user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_flight_crew_invites_invited_user_id_status",
        table_name="flight_crew_invites",
    )
    op.drop_index("ix_flight_crew_invites_flight_id", table_name="flight_crew_invites")
    op.drop_table("flight_crew_invites")
