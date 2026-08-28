"""AMP: split owner (block 1) from certifying party, real revision history

Fixes two gaps surfaced by comparing generated exports against real
shop-produced AMPs (OO-ICE, OO-CPE): block 1's "Owner" and block 8's
certifying party can be different real-world entities (a contracted
CAMO/CAO certifying on behalf of a different aircraft owner), and block 10
("Revision control & periodic reviews") is consistently a multi-row table
in practice (OO-CPE's real AMP has 3 rows: Rev 0/1/2), not a single field.

AmpDeclaration.revision_number/revision_content/revision_date are replaced
by a new one-to-many AmpRevision table (aircraft_id FK, mirrors
MaintenanceRecord's shape). Any existing single revision value is carried
forward as that aircraft's one AmpRevision row before the old columns are
dropped, so no revision data is lost.

Revision ID: f98e00a07cc7
Revises: 9e2b8243ce12
Create Date: 2026-08-28 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "f98e00a07cc7"
down_revision = "9e2b8243ce12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "amp_declarations", sa.Column("owner_name", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "amp_declarations", sa.Column("owner_address", sa.Text(), nullable=True)
    )

    op.create_table(
        "amp_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("aircraft_id", sa.Integer(), nullable=False),
        sa.Column("revision_number", sa.String(length=16), nullable=False),
        sa.Column("revision_content", sa.Text(), nullable=True),
        sa.Column("revision_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["aircraft_id"], ["aircraft.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_amp_revisions_aircraft_id", "amp_revisions", ["aircraft_id"]
    )

    declarations = sa.table(
        "amp_declarations",
        sa.column("aircraft_id", sa.Integer),
        sa.column("revision_number", sa.String),
        sa.column("revision_content", sa.String),
        sa.column("revision_date", sa.Date),
    )
    revisions = sa.table(
        "amp_revisions",
        sa.column("aircraft_id", sa.Integer),
        sa.column("revision_number", sa.String),
        sa.column("revision_content", sa.Text),
        sa.column("revision_date", sa.Date),
        sa.column("created_at", sa.DateTime),
    )

    conn = op.get_bind()
    now = sa.func.now()
    rows = conn.execute(
        sa.select(
            declarations.c.aircraft_id,
            declarations.c.revision_number,
            declarations.c.revision_content,
            declarations.c.revision_date,
        ).where(declarations.c.revision_number.isnot(None))
    )
    for aircraft_id, revision_number, revision_content, revision_date in rows:
        conn.execute(
            revisions.insert().values(
                aircraft_id=aircraft_id,
                revision_number=revision_number,
                revision_content=revision_content,
                revision_date=revision_date,
                created_at=now,
            )
        )

    op.drop_column("amp_declarations", "revision_number")
    op.drop_column("amp_declarations", "revision_content")
    op.drop_column("amp_declarations", "revision_date")


def downgrade() -> None:
    op.add_column(
        "amp_declarations", sa.Column("revision_number", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "amp_declarations",
        sa.Column("revision_content", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "amp_declarations", sa.Column("revision_date", sa.Date(), nullable=True)
    )

    declarations = sa.table(
        "amp_declarations",
        sa.column("aircraft_id", sa.Integer),
        sa.column("revision_number", sa.String),
        sa.column("revision_content", sa.String),
        sa.column("revision_date", sa.Date),
    )
    revisions = sa.table(
        "amp_revisions",
        sa.column("id", sa.Integer),
        sa.column("aircraft_id", sa.Integer),
        sa.column("revision_number", sa.String),
        sa.column("revision_content", sa.Text),
        sa.column("revision_date", sa.Date),
    )

    # Lossy the same way other multi-row-to-scalar downgrades in this
    # project are (e.g. 962109a45d5a): a repeating history has no single
    # row to unambiguously collapse into, so the downgrade keeps only the
    # most recently added revision per aircraft.
    conn = op.get_bind()
    latest_per_aircraft = conn.execute(
        sa.select(
            revisions.c.aircraft_id,
            revisions.c.revision_number,
            revisions.c.revision_content,
            revisions.c.revision_date,
        )
        .order_by(revisions.c.aircraft_id, revisions.c.id.desc())
        .distinct(revisions.c.aircraft_id)
    )
    for aircraft_id, revision_number, revision_content, revision_date in (
        latest_per_aircraft
    ):
        conn.execute(
            declarations.update()
            .where(declarations.c.aircraft_id == aircraft_id)
            .values(
                revision_number=revision_number,
                revision_content=revision_content,
                revision_date=revision_date,
            )
        )

    op.drop_index("ix_amp_revisions_aircraft_id", table_name="amp_revisions")
    op.drop_table("amp_revisions")

    op.drop_column("amp_declarations", "owner_address")
    op.drop_column("amp_declarations", "owner_name")
