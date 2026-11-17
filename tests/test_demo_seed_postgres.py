"""Run the demo seed's wipe-and-reseed on PostgreSQL as well: its foreign
keys (passports, airfield identities used as aircraft home airfields) are
enforced there, unlike in the default SQLite test database."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from tests.postgres_schema import postgres_app
from tests.test_demo_seed import TestDemoSeedEndToEnd

__all__ = ["TestDemoSeedEndToEnd"]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)
