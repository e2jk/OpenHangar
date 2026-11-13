"""Hostile writes on real objects leave them untouched (audit T03).

The route sweeps (tests/functional/test_tenant_isolation_sweep.py,
test_role_write_matrix.py) check reads and decorator ordering with mostly
nonexistent ids. Here every attempt targets an object that exists and a
payload the route would accept from its owner:

- another tenant's flight, document, upload file, pending reconcile row,
  import batch and renter account;
- a same-tenant pilot's own logbook entry and import batch;
- an aircraft the pilot's grant no longer covers;
- a parent from tenant A with a child from tenant B, and a forged aircraft
  id in a form.

The expected outcome of each is written here (the policy), not derived from
the routes' decorators. A denied call must return its expected status and
leave the database and files unchanged; each case has a matching control
where the rightful user does the same thing and it really happens (no 500).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from models import (
    Aircraft,
    AirframeImportBatch,
    BillingAccount,
    Document,
    Flight,
    LedgerEntry,
    LogbookImportBatch,
    PendingReconcile,
    Role,
    Tenant,
    TenantUser,
    User,
    UserAircraftAccess,
    db,
)

DENIED = (403, 404)


@dataclass
class World:
    admin_a: int
    pilot_1: int
    pilot_2: int
    admin_b: int
    renter_b: int
    aircraft_a: int
    aircraft_b: int
    flight_a: int
    flight_b: int
    doc_a: int
    doc_b: int
    doc_b_path: Path
    pending_a: int
    pending_b: int
    entry_p1: int
    batch_p1: int
    airframe_batch_b: int


def _user(email: str, tenant_id: int, role: Role, **flags) -> int:
    user = User(email=email, password_hash="unused", is_active=True, **flags)
    db.session.add(user)
    db.session.flush()
    db.session.add(TenantUser(user_id=user.id, tenant_id=tenant_id, role=role))
    return user.id


@pytest.fixture
def world(app) -> World:
    uploads = Path(app.config["UPLOAD_FOLDER"])
    with app.app_context():
        ta = Tenant(name="Tenant A", slug="tenant-a")
        tb = Tenant(name="Tenant B", slug="tenant-b")
        db.session.add_all([ta, tb])
        db.session.flush()
        admin_a = _user("admin@a.test", ta.id, Role.ADMIN, is_pilot=True)
        pilot_1 = _user("pilot1@a.test", ta.id, Role.PILOT, is_pilot=True)
        pilot_2 = _user("pilot2@a.test", ta.id, Role.PILOT, is_pilot=True)
        admin_b = _user("admin@b.test", tb.id, Role.ADMIN, is_pilot=True)
        renter_b = _user("renter@b.test", tb.id, Role.PILOT)
        ac_a = Aircraft(
            tenant_id=ta.id, registration="OO-AAA", make="Robin", model="DR400"
        )
        ac_b = Aircraft(
            tenant_id=tb.id, registration="OO-BBB", make="Cessna", model="172"
        )
        db.session.add_all([ac_a, ac_b])
        db.session.flush()
        db.session.add(UserAircraftAccess(user_id=pilot_1, aircraft_id=ac_a.id))
        flight_a = Flight(
            aircraft_id=ac_a.id,
            date=date(2026, 5, 1),
            departure_icao="EBAW",
            arrival_icao="EBKT",
        )
        flight_b = Flight(
            aircraft_id=ac_b.id,
            date=date(2026, 5, 2),
            departure_icao="EBBR",
            arrival_icao="EBCI",
            notes="B ORIGINAL",
        )
        batch_p1 = LogbookImportBatch(
            pilot_user_id=pilot_1,
            source_filename="p1.xlsx",
            imported_at=datetime.now(UTC),
        )
        airframe_batch_b = AirframeImportBatch(
            aircraft_id=ac_b.id, source_filename="b.xlsx", imported_at=datetime.now(UTC)
        )
        db.session.add_all([flight_a, flight_b, batch_p1, airframe_batch_b])
        db.session.flush()
        entry_p1 = Flight(
            pic_user_id=pilot_1,
            date=date(2026, 4, 1),
            other_aircraft_registration="OO-OTH",
            departure_icao="EBAW",
            arrival_icao="EBAW",
            notes="P1 ORIGINAL",
            import_batch_id=batch_p1.id,
        )
        db.session.add(entry_p1)
        docs = {}
        for key, tenant, ac in (("a", ta, ac_a), ("b", tb, ac_b)):
            rel = f"{tenant.slug}/{ac.registration}/other/2026-01-01 - Secret {key}.pdf"
            path = uploads / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"%PDF secret of tenant {key}".encode())
            doc = Document(
                aircraft_id=ac.id,
                filename=rel,
                original_filename=f"secret-{key}.pdf",
                category="other",
            )
            db.session.add(doc)
            pending = PendingReconcile(
                tenant_id=tenant.id, filepath=f"{tenant.slug}/inbox/{key}.pdf"
            )
            db.session.add(pending)
            db.session.flush()
            docs[key] = (doc.id, path, pending.id)
        db.session.commit()
        return World(
            admin_a=admin_a,
            pilot_1=pilot_1,
            pilot_2=pilot_2,
            admin_b=admin_b,
            renter_b=renter_b,
            aircraft_a=ac_a.id,
            aircraft_b=ac_b.id,
            flight_a=flight_a.id,
            flight_b=flight_b.id,
            doc_a=docs["a"][0],
            doc_b=docs["b"][0],
            doc_b_path=docs["b"][1],
            pending_a=docs["a"][2],
            pending_b=docs["b"][2],
            entry_p1=entry_p1.id,
            batch_p1=batch_p1.id,
            airframe_batch_b=airframe_batch_b.id,
        )


def _as(client, uid: int):
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return client


def _exists(app, model, pk) -> bool:
    with app.app_context():
        return db.session.get(model, pk) is not None


# ── Another tenant's objects ──────────────────────────────────────────────────


def test_flight_of_another_tenant_cannot_be_deleted(app, client, world):
    _as(client, world.admin_a)
    for ac in (
        world.aircraft_a,
        world.aircraft_b,
    ):  # parent A + child B, and B outright
        r = client.post(f"/aircraft/{ac}/flights/{world.flight_b}/delete")
        assert r.status_code in DENIED
    assert _exists(app, Flight, world.flight_b)
    # Control: the same action on their own flight happens.
    r = client.post(f"/aircraft/{world.aircraft_a}/flights/{world.flight_a}/delete")
    assert r.status_code == 302
    assert not _exists(app, Flight, world.flight_a)


def test_document_of_another_tenant_cannot_be_deleted_or_downloaded(app, client, world):
    _as(client, world.admin_a)
    for ac in (world.aircraft_a, world.aircraft_b):
        r = client.post(f"/aircraft/{ac}/documents/{world.doc_b}/delete")
        assert r.status_code in DENIED
    assert _exists(app, Document, world.doc_b)
    assert world.doc_b_path.read_bytes() == b"%PDF secret of tenant b"
    with app.app_context():
        rel = db.session.get(Document, world.doc_b).filename
    r = client.get(f"/uploads/{rel}")
    assert r.status_code in DENIED
    assert b"secret of tenant b" not in r.data
    # Control.
    r = client.post(f"/aircraft/{world.aircraft_a}/documents/{world.doc_a}/delete")
    assert r.status_code == 302
    assert not _exists(app, Document, world.doc_a)


def test_reconcile_rows_of_another_tenant_cannot_be_imported_or_ignored(
    app, client, world
):
    _as(client, world.admin_a)
    for action in ("import", "ignore"):
        r = client.post(
            f"/documents/reconcile/{world.pending_b}/{action}",
            data={"aircraft_id": world.aircraft_a, "category": "other"},
        )
        assert r.status_code in DENIED
    with app.app_context():
        assert db.session.get(PendingReconcile, world.pending_b).ignored is False
        assert Document.query.filter_by(aircraft_id=world.aircraft_a).count() == 1
    # Control.
    r = client.post(f"/documents/reconcile/{world.pending_a}/ignore")
    assert r.status_code == 302
    with app.app_context():
        assert db.session.get(PendingReconcile, world.pending_a).ignored is True


def test_import_batch_of_another_tenants_aircraft_cannot_be_rolled_back(
    app, client, world
):
    _as(client, world.admin_a)
    for ac in (world.aircraft_a, world.aircraft_b):
        r = client.post(
            f"/aircraft/{ac}/flights/import/{world.airframe_batch_b}/rollback"
        )
        assert r.status_code in DENIED
    assert _exists(app, AirframeImportBatch, world.airframe_batch_b)


def test_payment_on_another_tenants_renter_account_is_refused(app, client, world):
    _as(client, world.admin_a)
    r = client.post(
        f"/config/renters/{world.renter_b}/account/payment",
        data={"amount": "100", "date": "2026-05-01", "note": "forged"},
    )
    assert r.status_code in DENIED
    with app.app_context():
        assert LedgerEntry.query.count() == 0
        assert BillingAccount.query.filter_by(user_id=world.renter_b).count() == 0


# ── A same-tenant pilot's own records ─────────────────────────────────────────


def test_another_pilots_logbook_entry_cannot_be_edited_or_deleted(app, client, world):
    _as(client, world.pilot_2)
    r = client.post(
        f"/pilot/logbook/{world.entry_p1}/edit",
        data={
            "date": "2026-04-01",
            "remarks": "TAMPERED",
            "departure_place": "EBAW",
            "arrival_place": "EBAW",
        },
    )
    assert r.status_code in DENIED
    r = client.post(f"/pilot/logbook/{world.entry_p1}/delete")
    assert r.status_code in DENIED
    with app.app_context():
        assert db.session.get(Flight, world.entry_p1).notes == "P1 ORIGINAL"


def test_another_pilots_import_batch_cannot_be_rolled_back(app, client, world):
    _as(client, world.pilot_2)
    r = client.post(f"/pilot/logbook/import/{world.batch_p1}/rollback")
    assert r.status_code in DENIED
    assert _exists(app, LogbookImportBatch, world.batch_p1)
    assert _exists(app, Flight, world.entry_p1)
    # Control: the pilot who imported it rolls it back.
    _as(client, world.pilot_1)
    r = client.post(f"/pilot/logbook/import/{world.batch_p1}/rollback")
    assert r.status_code == 302
    assert not _exists(app, LogbookImportBatch, world.batch_p1)
    assert not _exists(app, Flight, world.entry_p1)


# ── Grants and forged ids ─────────────────────────────────────────────────────


def _new_flight(client, aircraft_id: int):
    with patch("services.notification_service.dispatch"):
        return client.post(
            "/flights/new",
            data={
                "aircraft_id": str(aircraft_id),
                "date": "2026-06-01",
                "departure_icao": "EBAW",
                "arrival_icao": "EBKT",
                "flight_time": "1.0",
                "pilot_role": "pic",
                "crew_name_0": "Test Pilot",
            },
        )


def _flights_on(app, aircraft_id: int) -> int:
    with app.app_context():
        return Flight.query.filter_by(aircraft_id=aircraft_id).count()


def test_a_revoked_grant_stops_logging_on_that_aircraft(app, client, world):
    before = _flights_on(app, world.aircraft_a)
    _as(client, world.pilot_1)
    r = _new_flight(client, world.aircraft_a)
    assert r.status_code == 302  # control
    assert _flights_on(app, world.aircraft_a) == before + 1
    with app.app_context():
        UserAircraftAccess.query.filter_by(user_id=world.pilot_1).delete()
        db.session.commit()
    r = _new_flight(client, world.aircraft_a)
    assert r.status_code != 500
    assert _flights_on(app, world.aircraft_a) == before + 1


def test_a_forged_aircraft_id_cannot_log_on_another_tenants_aircraft(
    app, client, world
):
    before = _flights_on(app, world.aircraft_b)
    _as(client, world.admin_a)
    r = _new_flight(client, world.aircraft_b)
    assert r.status_code != 500
    assert _flights_on(app, world.aircraft_b) == before


def test_capability_flags_are_not_taken_from_the_form(app, client, world):
    """A pilot cannot grant themself admin rights or an aircraft by posting
    the fields an admin form uses."""
    _as(client, world.pilot_2)
    r = client.post(
        "/config/users/invite",
        data={"email": "friend@a.test", "role": "admin", "is_instance_admin": "1"},
    )
    assert r.status_code in DENIED
    with app.app_context():
        assert db.session.get(User, world.pilot_2).is_instance_admin is False
        assert (
            db.session.get(
                TenantUser,
                (
                    world.pilot_2,
                    TenantUser.query.filter_by(user_id=world.pilot_2).one().tenant_id,
                ),
            ).role
            == Role.PILOT
        )
