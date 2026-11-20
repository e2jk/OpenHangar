"""Public projections survive migration and cannot be silently downgraded."""

import pytest
from flask import Flask
from flask_migrate import downgrade
from models import db
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests.test_postgres_locks_and_migrations import (
    MIGRATIONS,
    _insert,
    _upgrade,
    empty_schema,
    test_migrations_build_the_schema_the_models_describe,
)

__all__ = ["empty_schema", "test_migrations_build_the_schema_the_models_describe"]


def test_public_projection_backfill_and_safe_downgrade(
    empty_schema: tuple[Flask, Engine],
) -> None:
    app, engine = empty_schema
    previous = "4e90f737358d"
    _upgrade(app, previous)
    tenant = _insert(engine, "tenants", name="Projection migration")
    user = _insert(
        engine, "users", email="projection@test.invalid", password_hash="unused"
    )
    passport = _insert(
        engine, "passports", tenant_id=tenant, owner_user_id=user, title="Legacy"
    )
    field = _insert(
        engine,
        "passport_airfield_identities",
        tenant_id=tenant,
        source="ourairports",
        source_id="123",
        ident="EBNM",
        name="Namur",
    )
    _insert(
        engine,
        "passport_airfields",
        tenant_id=tenant,
        passport_id=passport,
        airfield_id=field,
    )
    for code in ("EBNM", "PRIVATE"):
        _insert(
            engine,
            "passport_airfield_aliases",
            airfield_id=field,
            code=code,
        )
    _upgrade(app, "head")
    with engine.connect() as conn:
        assert dict(
            conn.execute(
                text("SELECT code, catalogue_source_id FROM passport_airfield_aliases")
            ).all()
        ) == {"EBNM": "123", "PRIVATE": None}
        assert conn.execute(
            text(
                "SELECT public_catalogue, classification_overrides FROM passport_airfields"
            )
        ).one() == (False, {})
    for assignment in (
        "public_catalogue = true",
        "classification_overrides = :classifications",
    ):
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE passport_airfields SET " + assignment),
                {"classifications": '{"ulm":true}'},
            )
        with app.app_context():
            with pytest.raises(SystemExit):
                downgrade(directory=MIGRATIONS, revision=previous)
            db.session.remove()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE passport_airfields SET public_catalogue=false, classification_overrides='{}'"
                )
            )
    with app.app_context():
        downgrade(directory=MIGRATIONS, revision=previous)
    _upgrade(app, "head")
