"""Explicit home airfields and aircraft cruise speed for destination discovery."""

from alembic import op
import sqlalchemy as sa

revision = "898a3093f9c0"
down_revision = "8e862ec0a689"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("aircraft", "pilot_profiles"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("home_airfield_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                f"fk_{table}_home_airfield",
                "passport_airfield_identities",
                ["home_airfield_id"],
                ["id"],
                ondelete="SET NULL",
            )
    with op.batch_alter_table("aircraft") as batch:
        batch.add_column(
            sa.Column("cruise_speed_knots", sa.Numeric(6, 2), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("aircraft") as batch:
        batch.drop_column("cruise_speed_knots")
    for table in ("pilot_profiles", "aircraft"):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_home_airfield", type_="foreignkey")
            batch.drop_column("home_airfield_id")
