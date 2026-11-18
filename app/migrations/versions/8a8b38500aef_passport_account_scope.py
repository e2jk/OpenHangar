"""Explicit passport-only membership without a broad tenant role."""

from alembic import op
import sqlalchemy as sa

revision = "8a8b38500aef"
down_revision = "2530f183a938"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tenant_users") as batch:
        batch.add_column(
            sa.Column(
                "access_scope", sa.String(24), nullable=False, server_default="full"
            )
        )
        batch.alter_column("role", nullable=True)
        batch.create_check_constraint(
            "ck_tenant_users_access_scope", "access_scope IN ('full', 'passport')"
        )
        batch.create_check_constraint(
            "ck_tenant_users_scoped_role",
            "(access_scope = 'full' AND role IS NOT NULL) OR (access_scope = 'passport' AND role IS NULL)",
        )


def downgrade() -> None:
    # Never grant OWNER/VIEWER merely to make a restricted account fit an old schema.
    if (
        op.get_bind()
        .execute(
            sa.text("SELECT count(*) FROM tenant_users WHERE access_scope != 'full'")
        )
        .scalar()
    ):
        raise RuntimeError(
            "Remove restricted memberships before downgrading account scopes."
        )
    with op.batch_alter_table("tenant_users") as batch:
        batch.drop_constraint("ck_tenant_users_scoped_role", type_="check")
        batch.drop_constraint("ck_tenant_users_access_scope", type_="check")
        batch.alter_column("role", nullable=False)
        batch.drop_column("access_scope")
