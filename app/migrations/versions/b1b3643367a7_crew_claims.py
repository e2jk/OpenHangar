"""Crew claims: a pilot asks to be added to a flight someone else logged
(backlog: let a second crew member claim their own slot on an existing
flight — phase 3, claim from the duplicate-flight warning)

Revision ID: b1b3643367a7
Revises: 2978bd3abf28
Create Date: 2026-09-14 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "b1b3643367a7"
down_revision = "2978bd3abf28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("flight_crew_invites", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "kind",
                sa.String(length=16),
                nullable=False,
                server_default="invite",
            )
        )
        batch_op.add_column(
            sa.Column("requested_role", sa.String(length=16), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("flight_crew_invites", schema=None) as batch_op:
        batch_op.drop_column("requested_role")
        batch_op.drop_column("kind")
