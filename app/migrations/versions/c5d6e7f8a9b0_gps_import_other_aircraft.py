"""GPS import: other-aircraft free-text fields on batch

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-05-26 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5d6e7f8a9b0"
down_revision: str = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Store free-text aircraft info when the GPS import was done for an aircraft
    # not maintained in this OpenHangar instance.
    op.add_column(
        "aircraft_gps_import_batches",
        sa.Column("other_aircraft_make_model", sa.String(128), nullable=True),
    )
    op.add_column(
        "aircraft_gps_import_batches",
        sa.Column("other_aircraft_registration", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("aircraft_gps_import_batches", "other_aircraft_registration")
    op.drop_column("aircraft_gps_import_batches", "other_aircraft_make_model")
