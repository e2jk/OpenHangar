"""Place airfield controls, map privacy and visit integration on PostgreSQL."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from tests.postgres_schema import postgres_app
from tests.test_place_airfield_pages import (
    catalogue,
    links,
    people,
    test_custom_airfield_shares_only_its_explicit_details_and_allows_missing_coordinates,
    test_map_links_filters_and_downloads_share_the_same_places,
    test_nearby_suggestions_are_explicit_and_manual_search_allows_closed_and_heliports,
    test_personal_members_can_link_only_visible_metadata_and_readers_cannot_manage,
    test_visit_panel_records_only_own_progress_and_readers_can_visit,
)

__all__ = [
    "catalogue",
    "links",
    "people",
    "test_custom_airfield_shares_only_its_explicit_details_and_allows_missing_coordinates",
    "test_map_links_filters_and_downloads_share_the_same_places",
    "test_nearby_suggestions_are_explicit_and_manual_search_allows_closed_and_heliports",
    "test_personal_members_can_link_only_visible_metadata_and_readers_cannot_manage",
    "test_visit_panel_records_only_own_progress_and_readers_can_visit",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)
