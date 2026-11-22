"""Passport-only accounts become Personal accounts (access_scope 'personal').

The account type now covers every personal feature (passports, Places, Where
next?), not only passports. Older backups restore through this migration too.

Revision ID: eba8e84fe90d
Revises: 38faafd5e24b
"""

import sqlalchemy as sa
from alembic import op

revision = "eba8e84fe90d"
down_revision = "38faafd5e24b"
branch_labels = None
depends_on = None


def _rename(old: str, new: str) -> None:
    scopes = f"'{new}', 'logbook'"
    with op.batch_alter_table("tenant_users") as batch:
        batch.drop_constraint("ck_tenant_users_scoped_role", type_="check")
        batch.drop_constraint("ck_tenant_users_access_scope", type_="check")
    op.execute(
        sa.text(
            "UPDATE tenant_users SET access_scope = :new WHERE access_scope = :old"
        ).bindparams(new=new, old=old)
    )
    with op.batch_alter_table("tenant_users") as batch:
        batch.create_check_constraint(
            "ck_tenant_users_access_scope",
            f"access_scope IN ('full', {scopes})",
        )
        batch.create_check_constraint(
            "ck_tenant_users_scoped_role",
            "(access_scope = 'full' AND role IS NOT NULL) OR "
            f"(access_scope IN ({scopes}) AND role IS NULL)",
        )


def upgrade() -> None:
    _rename("passport", "personal")


def downgrade() -> None:
    _rename("personal", "passport")
