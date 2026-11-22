"""Filtered map/list queries, private files and maintenance locks on PostgreSQL."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from flask import Flask
from models import Place, db
from places.cleanup import purge_retired
from places.management import photo_path
from sqlalchemy import text

from tests.postgres_schema import postgres_app
from tests.test_place_pages import (
    destinations,
    people,
    test_collection_and_tag_filters_remain_private,
    test_daily_purge_retains_recent_entries_and_rolls_back_on_failure,
    test_filters_drive_identical_list_and_map,
    test_link_only_prefill_capture_and_later_location,
    test_place_photo_and_forms_respect_readers_nonmembers_and_view_only,
)

__all__ = [
    "destinations",
    "people",
    "test_collection_and_tag_filters_remain_private",
    "test_daily_purge_retains_recent_entries_and_rolls_back_on_failure",
    "test_filters_drive_identical_list_and_map",
    "test_link_only_prefill_capture_and_later_location",
    "test_place_photo_and_forms_respect_readers_nonmembers_and_view_only",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)


def test_purge_skips_a_place_locked_by_another_transaction(
    app: Flask, destinations: dict[str, int]
) -> None:
    with app.app_context():
        place = db.session.get(Place, destinations["alpha"])
        place.deleted_at = datetime.now(UTC) - timedelta(days=31)
        filename = place.photo_filename
        db.session.commit()
        with db.engine.begin() as connection:
            connection.execute(
                text("SELECT id FROM places WHERE id=:id FOR UPDATE"),
                {"id": destinations["alpha"]},
            )
            assert purge_retired() == (0, 0)
            assert photo_path(filename).exists()
        assert purge_retired() == (1, 0)
        assert not photo_path(filename).exists()
