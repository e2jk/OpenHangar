"""Real PostgreSQL group scope and additive migration checks."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from tests.postgres_schema import postgres_app
from tests.test_passport_groups import (
    group,
    test_history_targets_without_retroactive_stamps_or_vouchers,
    test_own_colour_precedence_frequency_and_history_withdrawal,
    test_shared_period_is_frozen_but_name_edits_remain_available,
    test_three_states_and_overlapping_frequency,
)
from tests.test_passport_management import owner

__all__ = [
    "group",
    "owner",
    "test_history_targets_without_retroactive_stamps_or_vouchers",
    "test_own_colour_precedence_frequency_and_history_withdrawal",
    "test_shared_period_is_frozen_but_name_edits_remain_available",
    "test_three_states_and_overlapping_frequency",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)
