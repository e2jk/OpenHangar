"""Keep public catalogue targets independent from private corrections.

Revision ID: 5abc051fdcae
Revises: 4e90f737358d
"""

import sys

import sqlalchemy as sa
from alembic import op

revision = "5abc051fdcae"
down_revision = "4e90f737358d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "passport_airfield_aliases",
        sa.Column("catalogue_source_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "passport_airfields",
        sa.Column(
            "public_catalogue", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "passport_airfields",
        sa.Column(
            "classification_overrides", sa.JSON(), nullable=False, server_default="{}"
        ),
    )
    op.execute(
        sa.text("""
        UPDATE passport_airfield_aliases
        SET catalogue_source_id = (
            SELECT f.source_id FROM passport_airfield_identities f
            WHERE f.id = passport_airfield_aliases.airfield_id
        )
        WHERE EXISTS (
            SELECT 1 FROM passport_airfield_identities f
            WHERE f.id = passport_airfield_aliases.airfield_id
              AND f.source = 'ourairports' AND f.source_id IS NOT NULL
              AND (f.ident = passport_airfield_aliases.code OR f.icao_code = passport_airfield_aliases.code)
        )
    """)
    )


def downgrade() -> None:
    count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM passport_airfields WHERE public_catalogue OR CAST(classification_overrides AS TEXT) <> '{}'"
            )
        )
        .scalar()
    )
    if count:
        print(
            "Remove public catalogue targets and local classifications before downgrading; an older schema cannot preserve their privacy.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    with op.batch_alter_table("passport_airfields") as batch:
        batch.drop_column("classification_overrides")
        batch.drop_column("public_catalogue")
    with op.batch_alter_table("passport_airfield_aliases") as batch:
        batch.drop_column("catalogue_source_id")
