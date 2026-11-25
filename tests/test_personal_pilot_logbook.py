"""'Start my pilot logbook' on a passport-only account: same account, own
entries only, the instance owner's policy, and never any fleet access."""

import logging
from typing import Any

import pytest
from flask import Flask, url_for
from flask.testing import FlaskClient
from models import (
    Aircraft,
    Flight,
    Role,
    TenantProfile,
    TenantUser,
    User,
    db,
)
from passports.eligibility import passport_progress
from services.account_scope import personal_logbook

from tests.auth_helpers import set_membership_flags
from tests.test_passport_builder import signin
from tests.test_passport_management import owner

__all__ = ["owner"]

ENTRY = {
    "date": "2024-06-01",
    "aircraft_type": "C172S",
    "aircraft_registration": "OO-OWN",
    "departure_place": "EBOS",
    "arrival_place": "TEST",
    "single_pilot_se": "1.5",
    "landings_day": "1",
    "function_pic": "1.5",
}


@pytest.fixture
def personal(app: Flask, owner: dict[str, int]) -> dict[str, int]:
    """The passport owner becomes a passport-only friend; `other` administers."""
    with app.app_context():
        tu = TenantUser.query.filter_by(user_id=owner["user"]).one()
        tu.is_pilot = True  # a flag a restricted account never gets access from
        tu.role, tu.access_scope = None, "personal"
        TenantUser.query.filter_by(user_id=owner["other"]).one().role = Role.OWNER
        db.session.add_all(
            [
                TenantProfile(tenant_id=owner["tenant"], setup_complete=True),
                Aircraft(
                    tenant_id=owner["tenant"],
                    registration="OO-CLUB",
                    make="Test",
                    model="Test",
                ),
            ]
        )
        db.session.commit()
    return owner


def _scope(app: Flask, uid: int) -> str:
    with app.app_context():
        return str(TenantUser.query.filter_by(user_id=uid).one().access_scope)


def _policy(app: Flask, tenant: int, value: str) -> None:
    with app.app_context():
        TenantProfile.query.filter_by(
            tenant_id=tenant
        ).one().personal_logbook_policy = value
        db.session.commit()


def test_passport_only_account_cannot_reach_the_logbook_yet(
    client: FlaskClient, personal: dict[str, int]
) -> None:
    signin(client, personal["user"])
    assert client.get("/pilot/logbook").status_code == 403
    index = client.get("/passports/").text
    assert "Start my pilot logbook" in index and "Pilot logbook</a>" not in index
    target = client.get(
        f"/passports/{personal['passport']}/airfields/{personal['target']}"
    ).text
    assert "included in your passport data export" in target


def test_default_policy_leaves_activation_to_an_administrator(
    app: Flask, client: FlaskClient, personal: dict[str, int]
) -> None:
    signin(client, personal["user"])
    page = client.get("/passports/start-logbook").text
    assert "Ask them to enable it for your account." in page
    assert 'name="confirm"' not in page
    assert (
        client.post("/passports/start-logbook", data={"confirm": "yes"}).status_code
        == 403
    )
    assert _scope(app, personal["user"]) == "personal"


def test_self_start_keeps_the_session_and_fills_passports(
    app: Flask,
    client: FlaskClient,
    personal: dict[str, int],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _policy(app, personal["tenant"], "self")
    with app.app_context():
        version = db.session.get(User, personal["user"]).auth_version
    signin(client, personal["user"])
    page = client.get("/passports/start-logbook").text
    assert 'name="confirm"' in page
    assert "no access to this organisation’s aircraft" in page
    missing = client.post("/passports/start-logbook", data={})
    assert missing.status_code == 422
    assert "Confirm that you want to start your pilot logbook." in missing.text
    with caplog.at_level(logging.WARNING):
        done = client.post("/passports/start-logbook", data={"confirm": "yes"})
    assert done.status_code == 302 and done.headers["Location"].endswith(
        "/pilot/logbook"
    )
    assert "passports.start_logbook" in caplog.text
    assert _scope(app, personal["user"]) == "logbook"
    with app.app_context():
        # Widening to a personal logbook does not sign the account out.
        assert db.session.get(User, personal["user"]).auth_version == version
    # Already on: the page simply opens the logbook.
    assert client.get("/passports/start-logbook").status_code == 302
    logbook = client.get("/pilot/logbook").text
    assert "/pilot/logbook/new" in logbook
    for hidden in ("/pilot/logbook/import", "/pilot/gps-import", "/pilot/profile"):
        assert hidden not in logbook
    assert "Pilot logbook</a>" in logbook or "Pilot logbook</span>" in logbook
    assert client.post("/pilot/logbook/new", data=ENTRY).status_code == 302
    with app.app_context():
        entry = Flight.query.filter_by(pic_user_id=personal["user"]).one()
        assert entry.aircraft_id is None
        from models import Passport

        passport = db.session.get(Passport, personal["passport"])
        progress = passport_progress(passport, personal["user"])
        assert [row["visited"] for row in progress["rows"]] == [True]
        entry_id = entry.id
    assert client.get(f"/pilot/logbook/{entry_id}/view").status_code == 200
    assert client.get("/pilot/stats").status_code == 200
    assert "Start my pilot logbook" not in client.get("/passports/").text


@pytest.mark.parametrize(
    "endpoint",
    [
        "aircraft.list_aircraft",
        "flights.log_flight",
        "reservations.fleet_reservations",
        "maintenance.fleet_overview",
        "pilots.profile",
        "pilots.import_upload",
        "pilots.pilot_gps_import_upload",
        "pilots.pilot_tracks",
        "pilots.minimums_view",
        "offline.pilot_logbook",
        "users.list_users",
        "config.index",
    ],
)
def test_personal_logbook_grants_no_fleet_or_other_pages(
    app: Flask, client: FlaskClient, personal: dict[str, int], endpoint: str
) -> None:
    with app.app_context():
        TenantUser.query.filter_by(
            user_id=personal["user"]
        ).one().access_scope = "logbook"
        db.session.commit()
    with app.test_request_context():
        path = url_for(endpoint)
    signin(client, personal["user"])
    assert client.get(path).status_code == 403


def test_personal_logbook_cannot_open_another_pilots_entry(
    app: Flask, client: FlaskClient, personal: dict[str, int]
) -> None:
    with app.app_context():
        TenantUser.query.filter_by(
            user_id=personal["user"]
        ).one().access_scope = "logbook"
        other = Flight(pic_user_id=personal["other"], arrival_icao="EBBR")
        from datetime import date

        other.date = date(2024, 5, 1)
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    signin(client, personal["user"])
    assert client.get(f"/pilot/logbook/{other_id}/view").status_code == 404


def test_administrator_enables_and_removes_personal_logbooks(
    app: Flask,
    client: FlaskClient,
    personal: dict[str, int],
    caplog: pytest.LogCaptureFixture,
) -> None:
    signin(client, personal["other"])
    users = client.get("/config/users/").text
    assert "Enable personal logbook" in users and "No aircraft access" in users
    url = f"/config/users/{personal['user']}/personal-logbook"
    with caplog.at_level(logging.WARNING):
        assert client.post(url, data={"enable": "yes"}).status_code == 302
    assert "users.personal_logbook" in caplog.text
    assert _scope(app, personal["user"]) == "logbook"
    users = client.get("/config/users/").text
    assert "Personal account with pilot logbook" in users
    assert "Remove personal logbook access" in users
    with app.app_context():
        version = db.session.get(User, personal["user"]).auth_version
    assert client.post(url, data={"enable": "no"}).status_code == 302
    assert _scope(app, personal["user"]) == "personal"
    with app.app_context():
        # Narrowing access signs the account out everywhere.
        assert db.session.get(User, personal["user"]).auth_version != version
    # A full account or one's own account is never changed here.
    assert (
        client.post(f"/config/users/{personal['other']}/personal-logbook").status_code
        == 400
    )


def test_full_accounts_and_view_only_accounts(
    app: Flask, client: FlaskClient, personal: dict[str, int]
) -> None:
    _policy(app, personal["tenant"], "self")
    signin(client, personal["other"])
    response = client.get("/passports/start-logbook")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/pilot/logbook")
    with app.app_context():
        set_membership_flags(personal["user"], view_only=True)
        db.session.commit()
    signin(client, personal["user"])
    assert 'name="confirm"' not in client.get("/passports/start-logbook").text


def test_policy_setting_and_helpers(
    app: Flask, client: FlaskClient, personal: dict[str, int]
) -> None:
    signin(client, personal["other"])
    settings = client.get("/config/").text
    assert 'name="personal_logbook_policy"' in settings
    data: dict[str, Any] = {
        "operating_model": "sole_operator",
        "personal_logbook_policy": "self",
    }
    assert client.post("/config/profile", data=data).status_code == 302
    with app.app_context():
        profile = TenantProfile.query.filter_by(tenant_id=personal["tenant"]).one()
        assert profile.personal_logbook_policy == "self"
    client.post("/config/profile", data=data | {"personal_logbook_policy": "x"})
    with app.app_context():
        profile = TenantProfile.query.filter_by(tenant_id=personal["tenant"]).one()
        assert profile.personal_logbook_policy == "self"
        assert not personal_logbook(None)


def test_defensive_guards_without_a_membership_or_logbook(
    app: Flask, personal: dict[str, int]
) -> None:
    from flask import session
    from passports.logbook_routes import start_logbook
    from utils import require_pilot_access
    from werkzeug.exceptions import Forbidden

    @require_pilot_access
    def page() -> str:
        return "ok"

    with app.test_request_context():
        # A passport-only membership reached directly, past the allowlist.
        session["user_id"] = personal["user"]
        with pytest.raises(Forbidden):
            page()
        TenantUser.query.filter_by(
            user_id=personal["user"]
        ).one().access_scope = "logbook"
        db.session.flush()
        assert page() == "ok"
        db.session.rollback()
    with app.test_request_context():
        with app.app_context():
            loner = User(email="loner@test.example", password_hash="unused")
            db.session.add(loner)
            db.session.commit()
            loner_id = loner.id
        session["user_id"] = loner_id
        with pytest.raises(Forbidden):
            start_logbook.__wrapped__()  # type: ignore[attr-defined]
