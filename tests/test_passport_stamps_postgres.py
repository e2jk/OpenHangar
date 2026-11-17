"""Run live stamp and participant-mutation contracts on PostgreSQL as well."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from tests.postgres_schema import postgres_app
from tests.test_passport_eligibility import (
    test_closed_completion_projected_dates_and_stable_ties,
    test_live_stamps_dates_counts_aliases_and_mutations,
)
from tests.test_passport_management import owner
from tests.test_passport_stamps import test_manual_arrival_controls_and_permissions

__all__ = [
    "owner",
    "test_closed_completion_projected_dates_and_stable_ties",
    "test_live_stamps_dates_counts_aliases_and_mutations",
    "test_manual_arrival_controls_and_permissions",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)
