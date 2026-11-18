"""Nullable scoped roles, login revocation and safe migration rollback on PostgreSQL."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask
from flask_migrate import downgrade
from models import db
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from tests.postgres_schema import postgres_app
from tests.test_passport_account_scope import (
    scoped,
    test_only_passport_notifications_remain_visible_and_deliverable,
    test_scope_and_null_role_are_constrained,
    test_scope_change_revokes_existing_login,
)
from tests.test_passport_management import owner
from tests.test_postgres_locks_and_migrations import (
    MIGRATIONS,
    _insert,
    _upgrade,
    empty_schema,
)

__all__ = [
    "empty_schema",
    "owner",
    "scoped",
    "test_only_passport_notifications_remain_visible_and_deliverable",
    "test_scope_and_null_role_are_constrained",
    "test_scope_change_revokes_existing_login",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)


def test_scope_upgrade_preserves_roles_and_downgrade_never_promotes_accounts(
    empty_schema: tuple[Flask, Engine],
    capsys: pytest.CaptureFixture[str],
) -> None:
    app, engine = empty_schema
    previous = "2530f183a938"
    _upgrade(app, previous)
    tenant = _insert(engine, "tenants", name="Existing group")
    user = _insert(engine, "users", email="existing@scope.test", password_hash="unused")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tenant_users(user_id, tenant_id, role) VALUES (:u,:t,'PILOT')"
            ),
            {"u": user, "t": tenant},
        )
    _upgrade(app, "head")
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT role, access_scope FROM tenant_users WHERE user_id=:u"),
            {"u": user},
        ).one() == ("PILOT", "full")
    friend = _insert(engine, "users", email="friend@scope.test", password_hash="unused")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tenant_users(user_id, tenant_id, role, access_scope) VALUES (:u,:t,NULL,'passport')"
            ),
            {"u": friend, "t": tenant},
        )
    with app.app_context():
        with pytest.raises(SystemExit) as refused:
            downgrade(directory=MIGRATIONS, revision=previous)
        assert refused.value.code == 1
        assert "Remove restricted memberships" in capsys.readouterr().err
        db.session.remove()
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT role FROM tenant_users WHERE user_id=:u"), {"u": friend}
            ).scalar_one()
            is None
        )
    # Removing the disposable restricted membership explicitly makes rollback safe.
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tenant_users WHERE user_id=:u"), {"u": friend})
    with app.app_context():
        downgrade(directory=MIGRATIONS, revision=previous)
    assert "access_scope" not in {
        c["name"] for c in inspect(engine).get_columns("tenant_users")
    }
    _upgrade(app, "head")
