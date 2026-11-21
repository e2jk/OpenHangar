"""Visit frequency counts every separate qualifying arrival, and neither the
frequency view nor the edition priority changes completion or vouchers."""

import re
from datetime import date, time

from flask import Flask
from flask.testing import FlaskClient
from models import Passport, db
from passports.eligibility import passport_progress

from tests.test_passport_builder import signin
from tests.test_passport_eligibility import flight
from tests.test_passport_management import owner

__all__ = ["owner"]


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
    return re.findall(r"Your progress: <strong>[^<]*</strong>[^·]*", text)


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
