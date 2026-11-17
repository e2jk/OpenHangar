"""Import draft/provenance round trips and history retention on PostgreSQL."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from tests.postgres_schema import postgres_app
from tests.test_passport_foundation import catalogue
from tests.test_passport_imports import (
    test_long_code_lists_use_bounded_alias_queries,
    test_replace_preserves_history_and_cancel_keeps_original,
    test_staged_import_unknowns_and_saved_provenance,
)
from tests.test_passport_management import owner

__all__ = [
    "catalogue",
    "owner",
    "test_long_code_lists_use_bounded_alias_queries",
    "test_replace_preserves_history_and_cancel_keeps_original",
    "test_staged_import_unknowns_and_saved_provenance",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)
