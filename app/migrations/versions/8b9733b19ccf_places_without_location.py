"""Allow capturing a place before its name or location is known.

Revision ID: 8b9733b19ccf
Revises: eba8e84fe90d
"""

import sqlalchemy as sa
from alembic import op

revision = "8b9733b19ccf"
down_revision = "eba8e84fe90d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("places") as batch:
        batch.alter_column("title", existing_type=sa.String(200), nullable=True)
        batch.alter_column("latitude", existing_type=sa.Float(), nullable=True)
        batch.alter_column("longitude", existing_type=sa.Float(), nullable=True)
        batch.create_check_constraint(
            "ck_place_coordinate_pair",
            "(latitude IS NULL AND longitude IS NULL) OR (latitude IS NOT NULL AND longitude IS NOT NULL)",
        )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM places WHERE title IS NULL OR latitude IS NULL OR longitude IS NULL"
            )
        )
        .scalar_one()
    ):
        raise RuntimeError(
            "Fill in all place names and locations before downgrading; no captured places will be deleted."
        )
    with op.batch_alter_table("places") as batch:
        batch.drop_constraint("ck_place_coordinate_pair", type_="check")
        batch.alter_column("title", existing_type=sa.String(200), nullable=False)
        batch.alter_column("latitude", existing_type=sa.Float(), nullable=False)
        batch.alter_column("longitude", existing_type=sa.Float(), nullable=False)
