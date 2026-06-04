"""Document categories, tenant slug, and pending-reconcile queue for Syncthing integration

Revision ID: aa1b2c3d4e5f
Revises: c2d3e4f5a6b7
Create Date: 2026-06-04 00:00:00.000000

"""

import re as _re

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "aa1b2c3d4e5f"
down_revision: str = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def _slug_from_name(name: str) -> str:
    s = name.lower()
    s = _re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")[:64]


def upgrade() -> None:
    # ── tenants.slug ──────────────────────────────────────────────────────────
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.add_column(sa.Column("slug", sa.String(64), nullable=True))
        batch_op.create_unique_constraint("uq_tenants_slug", ["slug"])

    # Backfill: derive slug from existing tenant names
    conn = op.get_bind()
    tenants = conn.execute(sa.text("SELECT id, name FROM tenants")).fetchall()
    used: set[str] = set()
    for tid, name in tenants:
        base = _slug_from_name(name or "hangar")
        slug = base
        n = 1
        while slug in used:
            slug = f"{base}-{n}"
            n += 1
        used.add(slug)
        conn.execute(
            sa.text("UPDATE tenants SET slug = :slug WHERE id = :id"),
            {"slug": slug, "id": tid},
        )

    # ── documents.category ───────────────────────────────────────────────────
    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("category", sa.String(32), nullable=True))
        # Widen filename to accommodate subdirectory paths
        batch_op.alter_column("filename", type_=sa.String(512), existing_nullable=False)

    # ── pending_reconcile table ──────────────────────────────────────────────
    op.create_table(
        "pending_reconcile",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Integer,
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "aircraft_id",
            sa.Integer,
            sa.ForeignKey("aircraft.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("filepath", sa.String(512), nullable=False, unique=True),
        sa.Column("category", sa.String(32), nullable=True),
        sa.Column("title_hint", sa.String(255), nullable=True),
        sa.Column("date_hint", sa.Date, nullable=True),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ignored", sa.Boolean, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("pending_reconcile")

    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("category")
        batch_op.alter_column("filename", type_=sa.String(255), existing_nullable=False)

    with op.batch_alter_table("tenants") as batch_op:
        batch_op.drop_constraint("uq_tenants_slug", type_="unique")
        batch_op.drop_column("slug")
