"""Generic capabilities and additive publication schema on PostgreSQL."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask
from flask_migrate import downgrade
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests.postgres_schema import postgres_app
from tests.test_postgres_locks_and_migrations import (
    MIGRATIONS,
    _insert,
    _upgrade,
    empty_schema,
    test_migrations_build_the_schema_the_models_describe,
)
from tests.test_public_shares import test_lifecycle_and_resource_binding

__all__ = [
    "empty_schema",
    "test_lifecycle_and_resource_binding",
    "test_migrations_build_the_schema_the_models_describe",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)


def test_publication_columns_are_private_after_upgrade(
    empty_schema: tuple[Flask, Engine],
) -> None:
    app, engine = empty_schema
    previous = "5c3f59cd82cb"
    _upgrade(app, previous)
    tenant = _insert(engine, "tenants", name="Publication migration")
    user = _insert(
        engine, "users", email="publication@test.invalid", password_hash="unused"
    )
    passport = _insert(
        engine, "passports", tenant_id=tenant, owner_user_id=user, title="Private"
    )
    participant = _insert(
        engine,
        "passport_participants",
        passport_id=passport,
        user_id=user,
        display_name="Private name",
        is_active=True,
    )
    _upgrade(app, "head")
    with engine.connect() as conn:
        assert conn.execute(
            text(
                "SELECT public_shared_at, public_frequency_at, public_name "
                "FROM passport_participants WHERE id=:id"
            ),
            {"id": participant},
        ).one() == (None, None, None)
        assert (
            conn.execute(text("SELECT count(*) FROM public_shares")).scalar_one() == 0
        )
    with app.app_context():
        downgrade(directory=MIGRATIONS, revision=previous)
    _upgrade(app, "head")
