"""Reset links: only a digest is stored, a new link voids older ones, and
one link sets one password even when submitted twice at once."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from models import PasswordResetToken, Role, Tenant, TenantUser, User, db  # pyright: ignore[reportMissingImports]
from sqlalchemy import text


def _user(app, email="owner@reset.dev", instance_admin=False):
    with app.app_context():
        user = User(
            email=email,
            password_hash="x",
            is_active=True,
            is_instance_admin=instance_admin,
        )
        db.session.add(user)
        db.session.flush()
        tenant = Tenant(name=f"T {email}")
        db.session.add(tenant)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        db.session.commit()
        return user.id, tenant.id


def _token(app, uid):
    with app.app_context():
        row = PasswordResetToken(
            user_id=uid,
            generated_by_user_id=uid,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.session.add(row)
        db.session.commit()
        return row.id, row.raw_token


def test_only_the_digest_is_stored(app):
    uid, _ = _user(app)
    row_id, raw = _token(app, uid)
    with app.app_context():
        stored = db.session.get(PasswordResetToken, row_id).token
        assert stored != raw and stored == PasswordResetToken.digest(raw)
        assert PasswordResetToken.find(raw).id == row_id


def test_a_new_link_voids_the_older_unused_ones(app, client):
    admin, _ = _user(app, "ia@reset.dev", instance_admin=True)
    owner, tid = _user(app)
    old_id, old_raw = _token(app, owner)
    with client.session_transaction() as sess:
        sess["user_id"] = admin
    client.post(f"/config/tenants/{tid}/reset-password", data={"owner_user_id": owner})
    with app.app_context():
        assert db.session.get(PasswordResetToken, old_id).is_used
    resp = client.get(f"/reset-password/{old_raw}")
    assert resp.status_code == 302


def test_a_link_claimed_meanwhile_sets_no_password(app, client):
    uid, _ = _user(app)
    _row_id, raw = _token(app, uid)
    form = {
        "new_password": "a-long-password-1",
        "confirm_password": "a-long-password-1",
    }
    # The other submit claimed the link between the check and the update.
    with patch("flask_sqlalchemy.query.Query.update", return_value=0):
        resp = client.post(f"/reset-password/{raw}", data=form, follow_redirects=True)
    assert b"already been used" in resp.data
    with app.app_context():
        assert db.session.get(User, uid).password_hash == "x"


def test_migration_hashes_existing_tokens(app):
    path = next(Path("app/migrations/versions").glob("94cb253ea9ea_*.py"))
    spec = importlib.util.spec_from_file_location("hash_reset_tokens", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    uid, _ = _user(app)
    row_id, _raw = _token(app, uid)
    with app.app_context():
        with db.engine.begin() as conn:
            conn.execute(
                text("UPDATE password_reset_tokens SET token='legacy' WHERE id=:i"),
                {"i": row_id},
            )
        with (
            db.engine.begin() as conn,
            Operations.context(MigrationContext.configure(conn)),
        ):
            module.upgrade()
        db.session.expire_all()
        assert PasswordResetToken.find("legacy").id == row_id
        with (
            db.engine.begin() as conn,
            Operations.context(MigrationContext.configure(conn)),
        ):
            module.downgrade()
        db.session.expire_all()
        assert db.session.get(PasswordResetToken, row_id) is None
