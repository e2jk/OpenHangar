"""Visitor-preserving moves, tag merges and independent photo files on PostgreSQL."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from tests.postgres_schema import postgres_app
from tests.test_place_management import (
    people,
    test_bin_restore_purge_and_thirty_day_expiry,
    test_collection_tag_rename_merge_delete_and_editor_boundary,
    test_copy_owns_independent_photo_tags_and_no_visits,
    test_move_requires_owner_and_every_visitor_and_remaps_tags,
)

__all__ = [
    "people",
    "test_bin_restore_purge_and_thirty_day_expiry",
    "test_collection_tag_rename_merge_delete_and_editor_boundary",
    "test_copy_owns_independent_photo_tags_and_no_visits",
    "test_move_requires_owner_and_every_visitor_and_remaps_tags",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)
