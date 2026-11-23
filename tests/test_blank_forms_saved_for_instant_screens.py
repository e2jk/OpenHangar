"""Blank new-data forms may be shown from a copy saved on the device (sw.js):
a date defaulting to today is marked so the browser sets its own today
(date_default_today.js); forms editing existing data never are."""

from datetime import date
from pathlib import Path

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Refuel,
    Role,
    Snag,
    Tenant,
    TenantUser,
    User,
    db,
)

_STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
MARK = b"data-oh-default-today"


def _setup(app, client):
    with app.app_context():
        tenant = Tenant(name="Blank forms")
        user = User(
            email="blank@example.com",
            password_hash=_pw_hash.hash("testpassword123"),
            is_active=True,
        )
        db.session.add_all([tenant, user])
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        ac = Aircraft(
            tenant_id=tenant.id, registration="OO-BLK", make="Cessna", model="172S"
        )
        db.session.add(ac)
        db.session.commit()
        uid, ac_id = user.id, ac.id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid, ac_id


def test_blank_forms_mark_their_default_date(app, client):
    _uid, ac_id = _setup(app, client)
    for url in (
        "/flights/new",
        f"/aircraft/{ac_id}/refuels/new",
        f"/aircraft/{ac_id}/snags/new",
        f"/aircraft/{ac_id}/expenses/add",
    ):
        response = client.get(url)
        assert response.status_code == 200, url
        assert MARK in response.data, url


def test_edit_forms_and_handed_over_dates_are_not_marked(app, client):
    _uid, ac_id = _setup(app, client)
    with app.app_context():
        refuel = Refuel(aircraft_id=ac_id, date=date(2026, 1, 2), quantity=10)
        snag = Snag(aircraft_id=ac_id, title="Loose screw")
        db.session.add_all([refuel, snag])
        db.session.commit()
        refuel_id, snag_id = refuel.id, snag.id
    for url in (
        f"/aircraft/{ac_id}/refuels/{refuel_id}/edit",
        f"/aircraft/{ac_id}/snags/{snag_id}/edit",
    ):
        response = client.get(url)
        assert response.status_code == 200, url
        assert MARK not in response.data, url
    # A date the user typed (a form shown again after an error) is kept.
    response = client.post("/flights/new", data={"date": "2026-01-02"})
    assert MARK not in response.data


def test_the_date_script_is_loaded_before_the_form_modules():
    base = (_STATIC.parent / "templates" / "base.html").read_text()
    assert base.index("js/date_default_today.js") < base.index("js/flight_form.js")
    script = (_STATIC / "js" / "date_default_today.js").read_text()
    assert "data-oh-default-today" in script
    assert "htmx:afterSettle" in script


def test_one_time_messages_are_marked_so_they_are_never_saved(app, client):
    _setup(app, client)
    with client.session_transaction() as sess:
        sess["_flashes"] = [("success", "Saved once")]
    response = client.get("/pilot/logbook")
    assert b"data-oh-flash" in response.data
    assert b"data-oh-flash" not in client.get("/pilot/logbook").data
    worker = (_STATIC / "js" / "sw.js").read_text()
    assert "var FLASH_MARKER = 'data-oh-flash';" in worker
    assert "body.indexOf(FLASH_MARKER) !== -1" in worker


def test_bottom_bar_pages_are_prefetched_first(app, client):
    _setup(app, client)
    html = client.get("/pilot/logbook").data.decode()
    first = html.split("data-oh-prefetch-first data-urls='", 1)[1].split("'", 1)[0]
    for path in ("/", "/aircraft/", "/flights/new", "/pilot/logbook", "/passports/"):
        assert f'"{path}"' in first, path
    prefetch = (_STATIC / "js" / "prefetch.js").read_text()
    assert "[data-oh-prefetch-first]" in prefetch
