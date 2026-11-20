"""Historical shared maps stay readable while a newer edition is current."""

from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient
from models import Passport, db

from tests.test_passport_builder import signin
from tests.test_passport_groups import group
from tests.test_passport_management import owner

__all__ = ["group", "owner"]


def test_historical_selection_with_another_current_participating_edition(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    with app.app_context():
        current = db.session.get(Passport, group["new"])
        today = datetime.now(UTC).date()
        current.start_date, current.end_date = (
            today - timedelta(days=1),
            today + timedelta(days=1),
        )
        db.session.commit()
    for uid in (group["user"], group["other"]):
        signin(client, uid)
        response = client.get(
            f"/passports/series/{group['series']}/",
            query_string={"edition": group["old"], "history": "1"},
        )
        assert response.status_code == 200
        assert 'value="' + str(group["old"]) + '" selected' in response.text
        assert (
            "PRIVATE MANUAL" not in response.text
            and "PRIVATE FLIGHT" not in response.text
        )
        if uid == group["other"]:
            assert "Read-only history; not a participant" in response.text
