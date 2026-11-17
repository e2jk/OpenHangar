"""Discovery defaults and HTTP flows against the production database type."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from tests.postgres_schema import postgres_app
from tests.test_passport_discovery import (
    test_discovery_page_and_aircraft_access,
    test_discovery_uses_published_reserve_and_new_aircraft_home,
)
from tests.test_passport_discovery_defaults import (
    test_home_forms_defaults_and_permissions,
)
from tests.test_passport_foundation import catalogue
from tests.test_passport_management import owner

__all__ = [
    "catalogue",
    "owner",
    "test_discovery_page_and_aircraft_access",
    "test_discovery_uses_published_reserve_and_new_aircraft_home",
    "test_home_forms_defaults_and_permissions",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)
