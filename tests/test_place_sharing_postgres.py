"""Collection invitation acceptance, transfer and schema parity on PostgreSQL."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from tests.postgres_schema import postgres_app
from tests.test_place_sharing import (
    people,
    test_collection_transfer_keeps_the_recipients_own_personal_collection,
    test_email_invitations_do_not_add_or_expose_existing_users_before_acceptance,
)
from tests.test_postgres_locks_and_migrations import (
    empty_schema,
    test_migrations_build_the_schema_the_models_describe,
)

__all__ = [
    "empty_schema",
    "people",
    "test_collection_transfer_keeps_the_recipients_own_personal_collection",
    "test_email_invitations_do_not_add_or_expose_existing_users_before_acceptance",
    "test_migrations_build_the_schema_the_models_describe",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)
