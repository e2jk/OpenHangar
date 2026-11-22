"""Concurrent members choosing the same airfield must leave one successful link."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from flask import Flask
from models import PassportAirfieldIdentity, PlaceAirfield, db
from passports import catalogue as airfield_catalogue
from places.candidates import select_airfield
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from tests.postgres_schema import postgres_app
from tests.test_place_airfield_pages import catalogue, links, people

__all__ = ["catalogue", "links", "people"]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)


@pytest.mark.parametrize("materialised", [False, True])
def test_concurrent_airfield_selection_is_idempotent(
    app: Flask,
    people: dict[str, int],
    links: dict[str, int],
    catalogue: list,
    materialised: bool,
) -> None:
    t, u, pid = people["tenant"], people["creator"], links["place"]
    with app.app_context():
        if materialised:
            identity = airfield_catalogue.materialise(t, catalogue[0])
            db.session.commit()
            key = f"field:{identity.id}"
            prefix = "update places set"
        else:
            key = "catalogue:near"
            prefix = "insert into passport_airfield_identities"
        engine = db.engine
    barrier = Barrier(2)

    def simultaneous_write(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _many: bool,
    ) -> None:
        if statement.lstrip().lower().startswith(prefix):
            barrier.wait(timeout=10)

    def choose(user_id: int) -> str:
        with app.app_context():
            try:
                select_airfield(t, user_id, pid, key)
                db.session.commit()
                return "ok"
            except IntegrityError:
                db.session.rollback()
                return "IntegrityError"

    event.listen(engine, "before_cursor_execute", simultaneous_write)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(choose, u), pool.submit(choose, people["personal"])]
            results = [future.result(timeout=20) for future in futures]
    finally:
        event.remove(engine, "before_cursor_execute", simultaneous_write)
    assert results == ["ok", "ok"]
    with app.app_context():
        assert PlaceAirfield.query.filter_by(place_id=pid).count() == 1
        assert (
            PassportAirfieldIdentity.query.filter_by(
                tenant_id=t, source_id="near"
            ).count()
            == 1
        )
