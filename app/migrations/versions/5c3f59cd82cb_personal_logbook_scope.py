"""Passport-only accounts that also keep their own pilot logbook.

Revision ID: 5c3f59cd82cb
Revises: 599f6d97deef
"""

import sqlalchemy as sa
from alembic import op

revision = "5c3f59cd82cb"
down_revision = "599f6d97deef"
branch_labels = None
depends_on = None


def _scope_constraints(scopes: str) -> None:
    with op.batch_alter_table("tenant_users") as batch:
        batch.drop_constraint("ck_tenant_users_scoped_role", type_="check")
        batch.drop_constraint("ck_tenant_users_access_scope", type_="check")
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
    _scope_constraints("'passport', 'logbook'")
    with op.batch_alter_table("tenant_profiles") as batch:
        batch.add_column(
            sa.Column(
                "personal_logbook_policy",
                sa.String(8),
                nullable=False,
                server_default="admin",
            )
        )


def downgrade() -> None:
    # The logbook entries stay; the account just loses access to them again.
    op.execute(
        "UPDATE tenant_users SET access_scope = 'passport' "
        "WHERE access_scope = 'logbook'"
    )
    _scope_constraints("'passport'")
    with op.batch_alter_table("tenant_profiles") as batch:
        batch.drop_column("personal_logbook_policy")
