"""Run the group-progress page and live-removal checks on PostgreSQL as well."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from tests.postgres_schema import postgres_app
from tests.test_passport_group_stats import (
    test_group_progress_page_ranks_filters_and_keeps_you,
    test_leaving_participant_drops_out_of_live_figures,
)
from tests.test_passport_groups import group
from tests.test_passport_management import owner

__all__ = [
    "group",
    "owner",
    "test_group_progress_page_ranks_filters_and_keeps_you",
    "test_leaving_participant_drops_out_of_live_figures",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)
