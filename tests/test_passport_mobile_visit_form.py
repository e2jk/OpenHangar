"""Minimal mobile visit form: choose the airfield and date, save; edits stay on
the airfield page, and only the participant's own passport is reachable."""

from datetime import UTC, date, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient
from models import Passport, PassportVisit, TenantUser, db

from tests.test_passport_builder import signin
from tests.test_passport_management import owner

__all__ = ["owner"]


def _path(owner: dict[str, int]) -> str:
    return f"/passports/{owner['passport']}/record-visit"


def test_form_lists_targets_and_saves_a_visit(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    signin(client, owner["user"])
    detail = client.get(f"/passports/{owner['passport']}/").text
    assert _path(owner) in detail
    page = client.get(_path(owner)).text
    assert f'<option value="{owner["target"]}"' in page and "First field" in page
    assert f'value="{date.today().isoformat()}"' in page
    # A pilot sees the logbook glimpse, and everyone the data export.
    assert "/pilot/logbook" in page and "/passports/export" in page
    response = client.post(
        _path(owner),
        data={
            "target": str(owner["target"]),
            "visit_date": date.today().isoformat(),
            "note": "Lunch stop",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        f"/passports/{owner['passport']}/airfields/{owner['target']}"
    )
    with app.app_context():
        visit = PassportVisit.query.one()
        assert visit.note == "Lunch stop" and visit.flight_id is None


def test_invalid_choices_are_explained_and_keep_the_input(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    signin(client, owner["user"])
    missing = client.post(_path(owner), data={"visit_date": "2025-01-01"})
    assert missing.status_code == 422
    assert "Choose an airfield of this passport." in missing.text
    future = (date.today() + timedelta(days=2)).isoformat()
    late = client.post(
        _path(owner),
        data={"target": str(owner["target"]), "visit_date": future, "note": "kept"},
    )
    assert late.status_code == 400
    assert "within the passport dates and not in the future" in late.text
    assert ">kept</textarea>" in late.text
    with app.app_context():
        assert PassportVisit.query.count() == 0


def test_other_accounts_and_archived_passports_are_refused(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    signin(client, owner["other"])
    assert client.get(_path(owner)).status_code in (403, 404)
    signin(client, owner["user"])
    with app.app_context():
        db.session.get(Passport, owner["passport"]).archived_at = datetime.now(UTC)
        db.session.commit()
    assert client.get(_path(owner)).status_code == 409
    assert _path(owner) not in client.get(f"/passports/{owner['passport']}/").text


def test_passport_only_account_sees_the_activation_path(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    with app.app_context():
        tu = TenantUser.query.filter_by(user_id=owner["user"]).one()
        tu.role, tu.access_scope = None, "personal"
        db.session.commit()
    signin(client, owner["user"])
    page = client.get(_path(owner)).text
    assert "/passports/start-logbook" in page and '/pilot/logbook"' not in page
