"""Visit frequency counts every separate qualifying arrival, and neither the
frequency view nor the edition priority changes completion or vouchers."""

import re
from datetime import date, time

from flask import Flask
from flask.testing import FlaskClient
from models import Passport, PassportAirfield, PassportAirfieldIdentity, db
from passports.catalogue import create_custom
from passports.eligibility import passport_progress

from tests.test_passport_builder import signin
from tests.test_passport_eligibility import flight
from tests.test_passport_management import owner

__all__ = ["owner"]


def test_airfield_list_sorts_by_visit_count_or_name(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    with app.app_context():
        for name, code, visits in (
            ("Zulu", "ZULU", 2),
            ("bravo", "BBBB", 0),
            ("Alpha", "AAAA", 1),
            ("Charlie", "CCCC", 0),
        ):
            field = create_custom(owner["tenant"], name, code=code)
            db.session.add(
                PassportAirfield(
                    tenant_id=owner["tenant"],
                    passport_id=owner["passport"],
                    airfield_id=field.id,
                )
            )
            for day in range(1, visits + 1):
                flight(owner["user"], date=date(2025, 3, day), arrival_icao=code)
        flight(owner["user"])
        db.session.commit()

    signin(client, owner["user"])
    path = f"/passports/{owner['passport']}/"
    for sort, expected in (
        ("", ["ZULU", "AAAA", "TEST", "BBBB", "CCCC"]),
        ("name", ["AAAA", "BBBB", "CCCC", "TEST", "ZULU"]),
    ):
        page = client.get(path, query_string={"sort": sort}).text
        table = page.split('id="passport-targets">', 1)[1].split("</table>", 1)[0]
        assert re.findall(r"\(([A-Z]{4})\)</a>", table) == expected


def test_closed_filter_depends_on_all_passport_airfields(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    signin(client, owner["user"])
    path = f"/passports/{owner['passport']}/"
    closed_option = '<option value="closed"'
    assert closed_option not in client.get(path).text
    stale_filter = client.get(path, query_string={"state": "closed"}).text
    assert '<option value="all" selected>' in stale_filter
    assert 'id="passport-targets"' in stale_filter

    with app.app_context():
        field = db.session.get(PassportAirfieldIdentity, owner["field"])
        field.overrides = {"is_closed": True}
        db.session.commit()

    visited_view = client.get(path, query_string={"state": "visited"}).text
    assert 'id="passport-targets"' not in visited_view
    assert closed_option in visited_view
    closed_view = client.get(path, query_string={"state": "closed"}).text
    assert '<option value="closed" selected>' in closed_view
    assert 'id="passport-targets"' in closed_view


def test_separate_same_day_arrivals_each_count(
    app: Flask, owner: dict[str, int]
) -> None:
    with app.app_context():
        flight(owner["user"], date=date(2025, 3, 1), arrival_time=time(10))
        flight(owner["user"], date=date(2025, 3, 1), arrival_time=time(15))
        db.session.flush()
        passport = db.session.get(Passport, owner["passport"])
        row = passport_progress(passport, owner["user"])["rows"][0]
        assert row["visited"] and row["count"] == 2
        # The earliest arrival of that day earns the stamp.
        assert str(row["first"]["time"]).startswith("10:00")


def _completion(text: str) -> list[str]:
    return re.findall(r"Your progress: <strong>[^<]*</strong>[^<]*", text)


def test_frequency_and_priority_never_change_completion(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    with app.app_context():
        for day in (1, 2, 3):
            flight(owner["user"], date=date(2025, 3, day))
        db.session.commit()
    signin(client, owner["user"])
    path = f"/passports/{owner['passport']}/"
    plain = client.get(path).text
    weighted = client.get(path, query_string={"frequency": "1"}).text
    oldest = client.get(path, query_string={"frequency": "1", "priority": "oldest"})
    assert 'data-frequency="0"' in plain and 'data-frequency="1"' in weighted
    assert _completion(plain) and _completion(plain) == _completion(weighted)
    assert _completion(weighted) == _completion(oldest.text)
