"""Edition copy, combined evidence and collection batching on PostgreSQL."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from tests.postgres_schema import postgres_app
from tests.test_passport_editions import (
    test_combined_identity_counts_dates_and_priority,
    test_explicit_copy_review_save_no_evidence_or_participants,
    test_link_and_series_http_privacy,
)
from tests.test_passport_eligibility import (
    test_collection_snapshot_equivalence_and_query_bounds,
)
from tests.test_passport_management import owner

__all__ = [
    "owner",
    "test_collection_snapshot_equivalence_and_query_bounds",
    "test_combined_identity_counts_dates_and_priority",
    "test_explicit_copy_review_save_no_evidence_or_participants",
    "test_link_and_series_http_privacy",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)
