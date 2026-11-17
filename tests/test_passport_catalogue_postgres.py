"""PostgreSQL cannot DISTINCT entire identity rows containing JSON overrides."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask
from models import Tenant, db
from passports.catalogue import add_alias, create_custom, resolve_code

from tests.postgres_schema import postgres_app


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)


def test_alias_resolution_with_json_and_duplicate_alias_rows(app: Flask) -> None:
    with app.app_context():
        tenant = Tenant(name="Alias regression")
        db.session.add(tenant)
        db.session.flush()
        field = create_custom(tenant.id, "Local strip", code="TEST")
        field.overrides = {"ulm": True}
        add_alias(field, "TEST")
        db.session.commit()
        resolved = resolve_code(tenant.id, "TEST")
        assert resolved is not None and resolved.id == field.id
        assert resolved.overrides == {"ulm": True}
