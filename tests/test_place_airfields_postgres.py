"""Place-airfield composite keys, cascades and projections on PostgreSQL."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask
from flask_migrate import downgrade
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from tests.postgres_schema import postgres_app
from tests.test_place_airfields import (
    links,
    people,
    test_composite_constraints_reject_foreign_airfields_and_wrong_collections,
    test_copy_and_move_preserve_links_and_source_metadata_choice,
    test_explicit_shared_private_link_grants_metadata_only_to_collection_members,
    test_link_notes_preference_distance_audit_and_idempotence,
    test_provider_filters_only_the_viewers_visits_and_handles_missing_locations,
    test_public_source_link_cannot_be_upgraded_to_private_metadata_by_id,
    test_readers_can_view_links_but_cannot_change_them,
    test_refused_note_and_missing_link_leave_existing_data_unchanged,
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
    "links",
    "people",
    "test_composite_constraints_reject_foreign_airfields_and_wrong_collections",
    "test_copy_and_move_preserve_links_and_source_metadata_choice",
    "test_explicit_shared_private_link_grants_metadata_only_to_collection_members",
    "test_link_notes_preference_distance_audit_and_idempotence",
    "test_migrations_build_the_schema_the_models_describe",
    "test_provider_filters_only_the_viewers_visits_and_handles_missing_locations",
    "test_public_source_link_cannot_be_upgraded_to_private_metadata_by_id",
    "test_readers_can_view_links_but_cannot_change_them",
    "test_refused_note_and_missing_link_leave_existing_data_unchanged",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)


def test_airfield_link_migration_preserves_places_and_can_be_reapplied(
    empty_schema: tuple[Flask, Engine],
) -> None:
    app, engine = empty_schema
    _upgrade(app, "8b9733b19ccf")
    tenant = _insert(engine, "tenants", name="Before airfield links")
    collection = _insert(
        engine, "place_collections", tenant_id=tenant, name="Ideas", is_personal=False
    )
    place = _insert(
        engine,
        "places",
        collection_id=collection,
        title="Existing place",
        latitude=None,
        longitude=None,
        archived=False,
    )
    _upgrade(app, "head")
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT title FROM places WHERE id=:id"), {"id": place}
            ).scalar_one()
            == "Existing place"
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM place_airfields")
            ).scalar_one()
            == 0
        )
    with app.app_context():
        downgrade(directory=MIGRATIONS, revision="8b9733b19ccf")
    assert "place_airfields" not in inspect(engine).get_table_names()
    _upgrade(app, "head")
    assert "place_airfields" in inspect(engine).get_table_names()
