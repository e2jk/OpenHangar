"""GPS track: several original files per track (source_file -> source_files)

Revision ID: f3581a6f0989
Revises: 71caf9252536
Create Date: 2026-11-07 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "f3581a6f0989"
down_revision = "71caf9252536"
branch_labels = None
depends_on = None

_tracks = sa.table(
    "gps_tracks",
    sa.column("id", sa.Integer),
    sa.column("source_file", sa.String),
    sa.column("source_files", sa.JSON),
)


def upgrade() -> None:
    with op.batch_alter_table("gps_tracks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_files", sa.JSON(), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(
        sa.select(_tracks.c.id, _tracks.c.source_file).where(
            _tracks.c.source_file.isnot(None)
        )
    ).all()
    for track_id, source_file in rows:
        conn.execute(
            _tracks.update()
            .where(_tracks.c.id == track_id)
            .values(source_files=[source_file])
        )
    with op.batch_alter_table("gps_tracks", schema=None) as batch_op:
        batch_op.drop_column("source_file")


def downgrade() -> None:
    with op.batch_alter_table("gps_tracks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("source_file", sa.String(length=512), nullable=True)
        )
    conn = op.get_bind()
    rows = conn.execute(
        sa.select(_tracks.c.id, _tracks.c.source_files).where(
            _tracks.c.source_files.isnot(None)
        )
    ).all()
    for track_id, files in rows:
        conn.execute(
            _tracks.update()
            .where(_tracks.c.id == track_id)
            .values(source_file=files[0] if files else None)
        )
    with op.batch_alter_table("gps_tracks", schema=None) as batch_op:
        batch_op.drop_column("source_files")
