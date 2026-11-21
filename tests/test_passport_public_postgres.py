"""Live publication controls and projections on PostgreSQL."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from tests.postgres_schema import postgres_app
from tests.test_passport_groups import group
from tests.test_passport_management import owner
from tests.test_passport_public import (
    test_explicit_photo_publication_and_rejoin,
    test_group_publication_is_each_participants_choice,
    test_live_resource_and_consent_epoch,
    test_public_allowlist_and_immediate_withdrawal,
    test_public_controls_errors_and_access,
    test_withdrawal_is_possible_for_view_only_participant,
)

__all__ = [
    "group",
    "owner",
    "test_explicit_photo_publication_and_rejoin",
    "test_group_publication_is_each_participants_choice",
    "test_live_resource_and_consent_epoch",
    "test_public_allowlist_and_immediate_withdrawal",
    "test_public_controls_errors_and_access",
    "test_withdrawal_is_possible_for_view_only_participant",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)
