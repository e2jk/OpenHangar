"""Collection retention, composite boundaries and additive schema on PostgreSQL."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask
from flask_migrate import downgrade
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from tests.postgres_schema import postgres_app
from tests.test_place_collections import (
    people,
    test_collection_delete_cascades_and_photo_cleanup_obeys_transaction,
    test_creator_account_removal_retains_content_without_stale_ownership,
    test_departure_retains_places_and_visits,
    test_tags_and_members_cannot_reference_another_collection_or_tenant,
)
from tests.test_postgres_locks_and_migrations import (
    MIGRATIONS,
    _insert,
    _upgrade,
    empty_schema,
    test_migrations_build_the_schema_the_models_describe,
)

__all__ = [
    "empty_schema",
    "people",
    "test_collection_delete_cascades_and_photo_cleanup_obeys_transaction",
    "test_creator_account_removal_retains_content_without_stale_ownership",
    "test_departure_retains_places_and_visits",
    "test_migrations_build_the_schema_the_models_describe",
    "test_tags_and_members_cannot_reference_another_collection_or_tenant",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)


def test_places_migration_is_additive_and_can_be_reapplied(
    empty_schema: tuple[Flask, Engine],
) -> None:
    app, engine = empty_schema
    _upgrade(app, "f81dbff14bed")
    tenant = _insert(engine, "tenants", name="Before Places")
    user = _insert(engine, "users", email="before@places.test", password_hash="unused")
    _upgrade(app, "head")
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT name FROM tenants WHERE id=:id"), {"id": tenant}
            ).scalar_one()
            == "Before Places"
        )
        assert (
            connection.execute(
                text("SELECT email FROM users WHERE id=:id"), {"id": user}
            ).scalar_one()
            == "before@places.test"
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM place_collections")
            ).scalar_one()
            == 0
        )
    with app.app_context():
        downgrade(directory=MIGRATIONS, revision="f81dbff14bed")
    assert "places" not in inspect(engine).get_table_names()
    _upgrade(app, "head")
    assert "places" in inspect(engine).get_table_names()


def test_optional_place_location_migration_preserves_data_and_guards_downgrade(
    empty_schema: tuple[Flask, Engine],
) -> None:
    app, engine = empty_schema
    _upgrade(app, "38faafd5e24b")
    tenant = _insert(engine, "tenants", name="Ideas")
    collection = _insert(
        engine, "place_collections", tenant_id=tenant, name="Ideas", is_personal=False
    )
    place = _insert(
        engine,
        "places",
        collection_id=collection,
        title="Known place",
        latitude=50.5,
        longitude=4.2,
        archived=False,
    )
    _upgrade(app, "head")
    with engine.begin() as connection:
        assert connection.execute(
            text("SELECT title, latitude, longitude FROM places WHERE id=:id"),
            {"id": place},
        ).one() == ("Known place", 50.5, 4.2)
        connection.execute(
            text(
                "UPDATE places SET title=NULL, latitude=NULL, longitude=NULL WHERE id=:id"
            ),
            {"id": place},
        )
    with (
        app.app_context(),
        pytest.raises(SystemExit),
    ):
        downgrade(directory=MIGRATIONS, revision="38faafd5e24b")
    with engine.begin() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM places WHERE id=:id AND latitude IS NULL"),
                {"id": place},
            ).scalar_one()
            == 1
        )
        connection.execute(
            text(
                "UPDATE places SET title='Located', latitude=0, longitude=0 WHERE id=:id"
            ),
            {"id": place},
        )
    with app.app_context():
        downgrade(directory=MIGRATIONS, revision="38faafd5e24b")
    _upgrade(app, "head")
