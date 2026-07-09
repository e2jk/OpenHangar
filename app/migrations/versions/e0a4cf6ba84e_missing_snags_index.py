"""Add the ix_snags_aircraft_id index missed by d569347024c6

The hot-path index migration declared indexes for every table in the
model change except snags — caught by the CI migration drift check.

Revision ID: e0a4cf6ba84e
Revises: c41f98b7c614
Create Date: 2026-07-09 10:00:00.000000
"""

from alembic import op

revision = "e0a4cf6ba84e"
down_revision = "c41f98b7c614"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_snags_aircraft_id", "snags", ["aircraft_id"])


def downgrade() -> None:
    op.drop_index("ix_snags_aircraft_id", table_name="snags")
