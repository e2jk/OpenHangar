"""Shared file lifetimes and account scope against PostgreSQL transactions."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from tests.postgres_schema import postgres_app
from tests.test_passport_management import owner
from tests.test_shared_services import (
    test_generic_account_export_uses_passport_provider_without_request_identity,
    test_registered_cleanup_refuses_path_traversal,
    test_registered_model_cleanup_is_folder_scoped_and_transactional,
)

__all__ = [
    "owner",
    "test_generic_account_export_uses_passport_provider_without_request_identity",
    "test_registered_cleanup_refuses_path_traversal",
    "test_registered_model_cleanup_is_folder_scoped_and_transactional",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)
