"""Store only a digest of password reset tokens

Reset links were stored as is, so a copy of the database or a backup gave
working links. The column now holds the SHA-256 digest of each token; links
already handed out keep working, since the link's token is hashed the same
way when it is used.

Revision ID: 94cb253ea9ea
Revises: 9bbf98ad0e8c
Create Date: 2026-11-26 00:00:00.000000
"""

import hashlib

import sqlalchemy as sa
from alembic import op

revision = "94cb253ea9ea"
down_revision = "9bbf98ad0e8c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, token FROM password_reset_tokens")).fetchall()
    for row_id, token in rows:
        bind.execute(
            sa.text("UPDATE password_reset_tokens SET token = :t WHERE id = :id"),
            {"t": hashlib.sha256(token.encode()).hexdigest(), "id": row_id},
        )


def downgrade() -> None:
    """Digests cannot be turned back into tokens: unused links stop working."""
    op.execute("DELETE FROM password_reset_tokens WHERE used_at IS NULL")
