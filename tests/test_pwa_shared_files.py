"""A file shared to OpenHangar from another app (the installed app's share
target) reaches the form the user picks — an expense receipt, a flight photo
or a maintenance service record — pre-attached, and is used when that form is
saved (pwa.pending)."""

import io
import os
import time
from datetime import date, timedelta

import pw_hash
import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import (
    Aircraft,
    DocCategory,
    Document,
    Expense,
    Flight,
    MaintenanceRecord,
    MaintenanceTrigger,
    Role,
    Tenant,
    TenantUser,
    TriggerType,
    User,
    db,
)
from pwa import pending
from services.temp_cleanup import cleanup_temp_uploads
from services.upload_layout import NOT_BACKED_UP_DIRNAMES, SHARE_TMP_DIRNAME

from tests.test_passport_stamps import image_bytes


def _member(app: Flask, email: str, role: str = Role.OWNER) -> tuple[int, int]:
    with app.app_context():
        tenant = Tenant(name=f"Org {email}")
        db.session.add(tenant)
        db.session.flush()
        user = User(email=email, password_hash=pw_hash.hash("x"), is_active=True)
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(tenant_id=tenant.id, user_id=user.id, role=role))
        ac = Aircraft(
            tenant_id=tenant.id, registration="OO-SHF", make="Test", model="T1"
        )
        db.session.add(ac)
        db.session.commit()
        return user.id, ac.id


def _login(client: FlaskClient, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def _jpeg(name: str = "IMG_2041") -> tuple[io.BytesIO, str, str]:
    # Android often sends a name without extension: the type decides it.
    return io.BytesIO(image_bytes()), name, "image/jpeg"


def _pdf(name: str = "invoice.pdf") -> tuple[io.BytesIO, str, str]:
    return io.BytesIO(b"%PDF-1.4 shared"), name, "application/pdf"


def _share(client: FlaskClient, *files: tuple[io.BytesIO, str, str]) -> bytes:
    response = client.post(
        "/pwa/shared",
        data={"files": list(files), "title": "Shared"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    return response.data


def _token(client: FlaskClient) -> str:
    with client.session_transaction() as sess:
        return str(sess[pending.SESSION_KEY]["token"])


def _share_folder(app: Flask, client: FlaskClient) -> str:
    return os.path.join(app.config["UPLOAD_FOLDER"], SHARE_TMP_DIRNAME, _token(client))


def _send(client: FlaskClient, destination: str, aircraft_id: int | None = None):
    data = {"destination": destination}
    if aircraft_id is not None:
        data["aircraft_id"] = str(aircraft_id)
    return client.post("/pwa/shared/confirm", data=data)


def _pending_in_session(client: FlaskClient) -> bool:
    with client.session_transaction() as sess:
        return pending.SESSION_KEY in sess


# ── The share and its files ──────────────────────────────────────────────────


def test_shared_files_are_kept_with_random_names_until_discarded(
    app: Flask, client: FlaskClient
) -> None:
    uid, _ac = _member(app, "keep@test.com")
    _login(client, uid)
    _share(client, _jpeg("../../etc/passwd"))
    folder = _share_folder(app, client)
    (saved,) = os.listdir(folder)
    assert saved.endswith(".jpg") and "passwd" not in saved
    # The share page can be opened again while the share is pending.
    again = client.get("/pwa/shared")
    assert again.status_code == 200 and b"passwd" in again.data
    response = client.post("/pwa/shared/discard")
    assert response.status_code == 302
    assert not os.path.exists(folder) and not _pending_in_session(client)
    assert client.get("/pwa/shared").status_code == 302


def test_a_new_share_replaces_the_previous_one(app: Flask, client: FlaskClient) -> None:
    uid, _ac = _member(app, "replace@test.com")
    _login(client, uid)
    _share(client, _pdf())
    first = _share_folder(app, client)
    _share(client, _jpeg())
    assert not os.path.exists(first) and os.path.isdir(_share_folder(app, client))


def test_upload_name_takes_the_extension_of_the_content_type() -> None:
    shared = pending.SharedFile(original="IMG 2041", saved="x", mime="image/png")
    assert shared.upload_name == "IMG_2041.png"
    odd = pending.SharedFile(original="???", saved="x", mime="text/x-unknown")
    assert odd.upload_name == "shared"


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(token="../../outside"),
        lambda d: d["files"][0].update(saved="../escape.jpg"),
        lambda d: d.update(user_id=999999),
        lambda d: d.update(files=[]),
        lambda d: d.update(user_id="not a number"),
        lambda d: d.pop("user_id"),
    ],
)
def test_a_tampered_or_foreign_share_is_ignored(
    app: Flask, client: FlaskClient, change
) -> None:
    uid, _ac = _member(app, f"tamper{id(change)}@test.com")
    _login(client, uid)
    _share(client, _jpeg())
    with client.session_transaction() as sess:
        data = sess[pending.SESSION_KEY]
        change(data)
        sess[pending.SESSION_KEY] = data
    assert client.get("/pwa/shared").status_code == 302


def test_a_share_whose_files_are_gone_is_ignored(
    app: Flask, client: FlaskClient
) -> None:
    uid, _ac = _member(app, "gone@test.com")
    _login(client, uid)
    _share(client, _jpeg())
    folder = _share_folder(app, client)
    for name in os.listdir(folder):
        os.remove(os.path.join(folder, name))
    assert client.get("/pwa/shared").status_code == 302
    with client.session_transaction() as sess:
        sess[pending.SESSION_KEY] = "not a share"
    assert client.get("/pwa/shared").status_code == 302


def test_abandoned_shares_are_cleaned_up_daily_and_never_backed_up(
    tmp_path,
) -> None:
    assert SHARE_TMP_DIRNAME in NOT_BACKED_UP_DIRNAMES
    stale = tmp_path / SHARE_TMP_DIRNAME / ("a" * 32)
    fresh = tmp_path / SHARE_TMP_DIRNAME / ("b" * 32)
    for folder in (stale, fresh):
        folder.mkdir(parents=True)
        (folder / "photo.jpg").write_bytes(b"x")
    two_days_ago = time.time() - 48 * 3600
    os.utime(stale / "photo.jpg", (two_days_ago, two_days_ago))
    os.utime(stale, (two_days_ago, two_days_ago))
    assert cleanup_temp_uploads(str(tmp_path)) == 1
    # Emptied by the file's removal: its folder goes too (it was old enough).
    os.utime(stale, (two_days_ago, two_days_ago))
    cleanup_temp_uploads(str(tmp_path))
    assert not stale.exists() and (fresh / "photo.jpg").exists()


# ── Which destinations are offered ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("role", "offered", "not_offered"),
    [
        (Role.OWNER, [b"dest-document", b"dest-expense", b"dest-flight_photo"], []),
        (Role.MAINTENANCE, [b"dest-maintenance"], [b"dest-document", b"dest-expense"]),
        (Role.PILOT, [b"dest-flight_photo"], [b"dest-document", b"dest-maintenance"]),
        (Role.VIEWER, [b"No compatible destination"], [b"dest-"]),
    ],
)
def test_destinations_follow_the_users_role(
    app: Flask, client: FlaskClient, role, offered, not_offered
) -> None:
    uid, _ac = _member(app, f"role-{role}@test.com", role=role)
    _login(client, uid)
    page = _share(client, _jpeg())
    for marker in offered:
        assert marker in page
    for marker in not_offered:
        assert marker not in page


def test_several_files_can_only_become_documents(
    app: Flask, client: FlaskClient
) -> None:
    uid, ac_id = _member(app, "several@test.com")
    _login(client, uid)
    page = _share(client, _pdf("a.pdf"), _pdf("b.pdf"))
    assert b"dest-document" in page and b"dest-expense" not in page
    response = _send(client, "document", ac_id)
    assert response.status_code == 302
    with app.app_context():
        assert Document.query.filter_by(aircraft_id=ac_id).count() == 2
    assert not _pending_in_session(client)


def test_flight_photo_needs_no_aircraft_and_stays_pending(
    app: Flask, client: FlaskClient
) -> None:
    uid, _ac = _member(app, "flightsend@test.com")
    _login(client, uid)
    _share(client, _jpeg())
    response = _send(client, "flight_photo")
    assert response.headers["Location"].endswith("/flights/new")
    assert _pending_in_session(client)


# ── Expense receipt ──────────────────────────────────────────────────────────

_EXPENSE = {
    "date": "2026-03-01",
    "expense_type": "fuel",
    "amount": "120.00",
    "currency": "EUR",
    "description": "Avgas",
}


def _shared_to_expense(app: Flask, client: FlaskClient, email: str) -> int:
    uid, ac_id = _member(app, email)
    _login(client, uid)
    _share(client, _pdf())
    response = _send(client, "expense", ac_id)
    assert response.headers["Location"].endswith("/expenses/add")
    return ac_id


def test_shared_receipt_is_saved_with_the_new_expense(
    app: Flask, client: FlaskClient
) -> None:
    ac_id = _shared_to_expense(app, client, "receipt@test.com")
    form = client.get(f"/aircraft/{ac_id}/expenses/add")
    assert b"Shared file:" in form.data and b"invoice.pdf" in form.data
    folder = _share_folder(app, client)
    response = client.post(
        f"/aircraft/{ac_id}/expenses/add",
        data={**_EXPENSE, "use_shared_file": _token(client)},
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    with app.app_context():
        expense = Expense.query.filter_by(aircraft_id=ac_id).one()
        (receipt,) = expense.receipts
        assert receipt.category == DocCategory.INVOICE
        assert receipt.original_filename == "invoice.pdf"
        assert os.path.isfile(
            os.path.join(app.config["UPLOAD_FOLDER"], receipt.filename)
        )
    assert not os.path.exists(folder) and not _pending_in_session(client)


def test_unticked_shared_receipt_is_not_used_and_stays_pending(
    app: Flask, client: FlaskClient
) -> None:
    ac_id = _shared_to_expense(app, client, "unticked@test.com")
    client.post(
        f"/aircraft/{ac_id}/expenses/add",
        data=_EXPENSE,
        content_type="multipart/form-data",
    )
    with app.app_context():
        assert Expense.query.filter_by(aircraft_id=ac_id).one().receipts == []
    assert _pending_in_session(client)


def test_an_uploaded_receipt_wins_over_the_shared_one(
    app: Flask, client: FlaskClient
) -> None:
    ac_id = _shared_to_expense(app, client, "uploadwins@test.com")
    client.post(
        f"/aircraft/{ac_id}/expenses/add",
        data={
            **_EXPENSE,
            "use_shared_file": _token(client),
            "receipt": (io.BytesIO(b"%PDF-1.4 mine"), "mine.pdf"),
        },
        content_type="multipart/form-data",
    )
    with app.app_context():
        (receipt,) = Expense.query.filter_by(aircraft_id=ac_id).one().receipts
        assert receipt.original_filename == "mine.pdf"
    assert _pending_in_session(client)


def test_an_invalid_expense_keeps_the_shared_receipt(
    app: Flask, client: FlaskClient
) -> None:
    ac_id = _shared_to_expense(app, client, "invalid@test.com")
    response = client.post(
        f"/aircraft/{ac_id}/expenses/add",
        data={**_EXPENSE, "amount": "", "use_shared_file": _token(client)},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert b"invoice.pdf" in response.data
    with app.app_context():
        assert Expense.query.filter_by(aircraft_id=ac_id).count() == 0
    assert _pending_in_session(client)


def test_a_stale_form_token_does_not_attach_a_newer_share(
    app: Flask, client: FlaskClient
) -> None:
    ac_id = _shared_to_expense(app, client, "stale@test.com")
    client.post(
        f"/aircraft/{ac_id}/expenses/add",
        data={**_EXPENSE, "use_shared_file": "0" * 32},
        content_type="multipart/form-data",
    )
    with app.app_context():
        assert Expense.query.filter_by(aircraft_id=ac_id).one().receipts == []


# ── Flight photo ─────────────────────────────────────────────────────────────


def _flight(ac_id: int, **extra) -> dict:
    return {
        "aircraft_id": str(ac_id),
        "date": "2026-06-01",
        "departure_icao": "EBOS",
        "arrival_icao": "EBBR",
        "flight_time_counter_start": "100.0",
        "flight_time_counter_end": "101.5",
        "crew_name_0": "Test Pilot",
        "crew_role_0": "PIC",
        **extra,
    }


def test_shared_photo_is_saved_in_the_chosen_slot(
    app: Flask, client: FlaskClient
) -> None:
    uid, ac_id = _member(app, "photo@test.com")
    _login(client, uid)
    _share(client, _jpeg())
    _send(client, "flight_photo")
    form = client.get("/flights/new")
    assert b"Use the shared photo as" in form.data
    response = client.post(
        "/flights/new",
        data=_flight(
            ac_id,
            use_shared_file=_token(client),
            shared_photo_slot="fuel_photo",
        ),
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    with app.app_context():
        flight = Flight.query.filter_by(aircraft_id=ac_id).one()
        assert flight.fuel_photo and flight.fuel_photo.endswith(".jpg")
        assert flight.flight_counter_photo is None
        assert os.path.isfile(
            os.path.join(app.config["UPLOAD_FOLDER"], flight.fuel_photo)
        )
    assert not _pending_in_session(client)


def test_a_photo_uploaded_in_that_slot_wins_and_the_share_stays(
    app: Flask, client: FlaskClient
) -> None:
    uid, ac_id = _member(app, "photowins@test.com")
    _login(client, uid)
    _share(client, _jpeg())
    _send(client, "flight_photo")
    client.post(
        "/flights/new",
        data=_flight(
            ac_id,
            use_shared_file=_token(client),
            shared_photo_slot="flight_counter_photo",
            flight_counter_photo=(io.BytesIO(image_bytes()), "mine.jpg"),
        ),
        content_type="multipart/form-data",
    )
    assert _pending_in_session(client)


@pytest.mark.parametrize("slot", ["", "not_a_photo_field"])
def test_an_unknown_photo_slot_attaches_nothing(
    app: Flask, client: FlaskClient, slot: str
) -> None:
    uid, ac_id = _member(app, f"badslot{slot}@test.com")
    _login(client, uid)
    _share(client, _jpeg())
    _send(client, "flight_photo")
    client.post(
        "/flights/new",
        data=_flight(ac_id, use_shared_file=_token(client), shared_photo_slot=slot),
        content_type="multipart/form-data",
    )
    with app.app_context():
        flight = Flight.query.filter_by(aircraft_id=ac_id).one()
        assert flight.fuel_photo is None and flight.flight_counter_photo is None
    assert _pending_in_session(client)


def test_shared_photo_with_an_extension_flights_refuse_is_not_attached(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import flights.routes as flight_routes

    monkeypatch.setattr(flight_routes, "_ALLOWED_PHOTO_EXTS", {".png"})
    uid, ac_id = _member(app, "refused@test.com")
    _login(client, uid)
    _share(client, _jpeg())
    _send(client, "flight_photo")
    client.post(
        "/flights/new",
        data=_flight(
            ac_id, use_shared_file=_token(client), shared_photo_slot="fuel_photo"
        ),
        content_type="multipart/form-data",
    )
    with app.app_context():
        assert Flight.query.filter_by(aircraft_id=ac_id).one().fuel_photo is None
    assert _pending_in_session(client)


# ── Maintenance service record ───────────────────────────────────────────────


def _trigger(app: Flask, ac_id: int) -> int:
    with app.app_context():
        trigger = MaintenanceTrigger(
            aircraft_id=ac_id,
            name="Annual",
            trigger_type=TriggerType.CALENDAR,
            due_date=date.today() + timedelta(days=60),
            interval_days=365,
        )
        db.session.add(trigger)
        db.session.commit()
        return trigger.id


def test_shared_file_is_saved_with_the_service_record(
    app: Flask, client: FlaskClient
) -> None:
    uid, ac_id = _member(app, "service@test.com", role=Role.MAINTENANCE)
    trigger_id = _trigger(app, ac_id)
    with app.app_context():
        from models import UserAllAircraftAccess

        tenant_id = db.session.get(Aircraft, ac_id).tenant_id
        db.session.add(UserAllAircraftAccess(user_id=uid, tenant_id=tenant_id))
        db.session.commit()
    _login(client, uid)
    _share(client, _pdf("crs.pdf"))
    response = _send(client, "maintenance", ac_id)
    assert response.headers["Location"].endswith("/maintenance")
    listing = client.get(f"/aircraft/{ac_id}/maintenance")
    assert b"shared-file-banner" in listing.data and b"crs.pdf" in listing.data
    service_url = f"/aircraft/{ac_id}/maintenance/{trigger_id}/service"
    assert b"crs.pdf" in client.get(service_url).data
    # A form error keeps the shared file attached.
    invalid = client.post(
        service_url,
        data={"performed_at": "", "use_shared_file": _token(client)},
    )
    assert invalid.status_code == 200 and b"crs.pdf" in invalid.data
    response = client.post(
        service_url,
        data={"performed_at": "2026-04-01", "use_shared_file": _token(client)},
    )
    assert response.status_code == 302
    with app.app_context():
        assert MaintenanceRecord.query.filter_by(trigger_id=trigger_id).count() == 1
        doc = Document.query.filter_by(aircraft_id=ac_id).one()
        assert doc.category == DocCategory.MAINTENANCE
        assert doc.title == "Annual, serviced 2026-04-01"
        assert doc.original_filename == "crs.pdf"
    assert not _pending_in_session(client)


def test_service_without_the_shared_file_keeps_it_pending(
    app: Flask, client: FlaskClient
) -> None:
    uid, ac_id = _member(app, "noservicefile@test.com")
    trigger_id = _trigger(app, ac_id)
    _login(client, uid)
    _share(client, _pdf())
    _send(client, "maintenance", ac_id)
    client.post(
        f"/aircraft/{ac_id}/maintenance/{trigger_id}/service",
        data={"performed_at": "2026-04-01"},
    )
    with app.app_context():
        assert Document.query.filter_by(aircraft_id=ac_id).count() == 0
    assert _pending_in_session(client)


def test_a_share_shows_only_on_the_form_and_aircraft_it_was_sent_to(
    app: Flask, client: FlaskClient
) -> None:
    ac_id = _shared_to_expense(app, client, "elsewhere@test.com")
    with app.app_context():
        tenant_id = db.session.get(Aircraft, ac_id).tenant_id
        other = Aircraft(
            tenant_id=tenant_id, registration="OO-OTR", make="T", model="M"
        )
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    assert b"Shared file:" not in client.get(f"/aircraft/{other_id}/expenses/add").data
    assert b"Shared file:" not in client.get("/flights/new").data
    assert b"Shared file:" in client.get(f"/aircraft/{ac_id}/expenses/add").data
