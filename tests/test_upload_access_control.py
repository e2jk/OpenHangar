"""
Tests for upload access control (IDOR fix on /uploads/<filename>).

Verifies that serve_upload enforces ownership before serving a file:
- Aircraft documents: accessible to the owning tenant, 404 to other tenants.
- Component documents: access checked via the component's aircraft/tenant.
- Flight-entry documents: access checked via the flight entry's aircraft/tenant.
- Pilot documents: accessible only to the holder, 404 to any other user (no info leakage).
- FlightEntry counter/fuel photos: tenant-scoped, 404 to other tenants.
- Unknown filename (no matching record): 404.
"""

import os
from datetime import date

import bcrypt  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Component,
    ComponentType,
    Document,
    FlightEntry,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_tenant_and_user(app, email):
    """Create a fresh tenant + admin user; return (user_id, tenant_id)."""
    with app.app_context():
        tenant = Tenant(name=f"Hangar-{email}")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()
        return user.id, tenant.id


def _login(client, app, email):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _make_aircraft(app, tenant_id, reg="OO-TST"):
    with app.app_context():
        ac = Aircraft(tenant_id=tenant_id, registration=reg, make="Cessna", model="172")
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _make_component(app, aircraft_id):
    with app.app_context():
        comp = Component(
            aircraft_id=aircraft_id,
            type=ComponentType.ENGINE,
            make="Lycoming",
            model="IO-360",
        )
        db.session.add(comp)
        db.session.commit()
        return comp.id


def _make_flight(app, aircraft_id):
    with app.app_context():
        fe = FlightEntry(
            aircraft_id=aircraft_id,
            date=date(2024, 1, 1),
            departure_icao="EBBR",
            arrival_icao="EBOS",
        )
        db.session.add(fe)
        db.session.commit()
        return fe.id


def _write_file(app, filename, content=b"data"):
    folder = app.config["UPLOAD_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, filename), "wb") as f:
        f.write(content)


def _make_aircraft_doc(app, aircraft_id, filename):
    with app.app_context():
        doc = Document(
            aircraft_id=aircraft_id,
            filename=filename,
            original_filename=filename,
            mime_type="application/pdf",
        )
        db.session.add(doc)
        db.session.commit()
        return doc.id


def _make_component_doc(app, aircraft_id, component_id, filename):
    with app.app_context():
        doc = Document(
            aircraft_id=aircraft_id,
            component_id=component_id,
            filename=filename,
            original_filename=filename,
            mime_type="application/pdf",
        )
        db.session.add(doc)
        db.session.commit()
        return doc.id


def _make_flight_doc(app, flight_entry_id, filename):
    with app.app_context():
        doc = Document(
            flight_entry_id=flight_entry_id,
            filename=filename,
            original_filename=filename,
            mime_type="image/jpeg",
        )
        db.session.add(doc)
        db.session.commit()
        return doc.id


def _make_pilot_doc(app, pilot_user_id, filename):
    with app.app_context():
        doc = Document(
            pilot_user_id=pilot_user_id,
            filename=filename,
            original_filename=filename,
            mime_type="application/pdf",
        )
        db.session.add(doc)
        db.session.commit()
        return doc.id


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestAircraftDocumentAccess:
    def test_owner_tenant_can_access(self, app, client):
        uid, tid = _make_tenant_and_user(app, "owner@a.com")
        acid = _make_aircraft(app, tid)
        _write_file(app, "ac_doc.pdf")
        _make_aircraft_doc(app, acid, "ac_doc.pdf")
        _login(client, app, "owner@a.com")
        assert client.get("/uploads/ac_doc.pdf").status_code == 200

    def test_other_tenant_gets_404(self, app, client):
        _, tid_a = _make_tenant_and_user(app, "a@a.com")
        _make_tenant_and_user(app, "b@b.com")
        acid = _make_aircraft(app, tid_a)
        _write_file(app, "ac_secret.pdf")
        _make_aircraft_doc(app, acid, "ac_secret.pdf")
        _login(client, app, "b@b.com")  # user from tenant B
        assert client.get("/uploads/ac_secret.pdf").status_code == 404

    def test_unauthenticated_redirects_to_login(self, client):
        assert client.get("/uploads/any.pdf").status_code == 302


class TestComponentDocumentAccess:
    def test_owner_tenant_can_access(self, app, client):
        uid, tid = _make_tenant_and_user(app, "comp_owner@a.com")
        acid = _make_aircraft(app, tid)
        cid = _make_component(app, acid)
        _write_file(app, "comp_doc.pdf")
        _make_component_doc(app, acid, cid, "comp_doc.pdf")
        _login(client, app, "comp_owner@a.com")
        assert client.get("/uploads/comp_doc.pdf").status_code == 200

    def test_other_tenant_gets_404(self, app, client):
        _, tid_a = _make_tenant_and_user(app, "ca@a.com")
        _make_tenant_and_user(app, "cb@b.com")
        acid = _make_aircraft(app, tid_a)
        cid = _make_component(app, acid)
        _write_file(app, "comp_secret.pdf")
        _make_component_doc(app, acid, cid, "comp_secret.pdf")
        _login(client, app, "cb@b.com")
        assert client.get("/uploads/comp_secret.pdf").status_code == 404


class TestFlightDocumentAccess:
    def test_owner_tenant_can_access(self, app, client):
        uid, tid = _make_tenant_and_user(app, "fe_owner@a.com")
        acid = _make_aircraft(app, tid)
        fid = _make_flight(app, acid)
        _write_file(app, "flight_doc.pdf")
        _make_flight_doc(app, fid, "flight_doc.pdf")
        _login(client, app, "fe_owner@a.com")
        assert client.get("/uploads/flight_doc.pdf").status_code == 200

    def test_other_tenant_gets_404(self, app, client):
        _, tid_a = _make_tenant_and_user(app, "fa@a.com")
        _make_tenant_and_user(app, "fb@b.com")
        acid = _make_aircraft(app, tid_a)
        fid = _make_flight(app, acid)
        _write_file(app, "flight_secret.pdf")
        _make_flight_doc(app, fid, "flight_secret.pdf")
        _login(client, app, "fb@b.com")
        assert client.get("/uploads/flight_secret.pdf").status_code == 404


class TestPilotDocumentAccess:
    def test_holder_can_access_own_document(self, app, client):
        uid, _ = _make_tenant_and_user(app, "pilot@a.com")
        _write_file(app, "my_license.pdf")
        _make_pilot_doc(app, uid, "my_license.pdf")
        _login(client, app, "pilot@a.com")
        assert client.get("/uploads/my_license.pdf").status_code == 200

    def test_other_user_same_tenant_gets_404(self, app, client):
        uid_a, tid = _make_tenant_and_user(app, "pilot_a@a.com")
        # Add a second user to the same tenant
        with app.app_context():
            user_b = User(
                email="pilot_b@a.com",
                password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(user_b)
            db.session.flush()
            db.session.add(
                TenantUser(user_id=user_b.id, tenant_id=tid, role=Role.ADMIN)
            )
            db.session.commit()
        _write_file(app, "private_license.pdf")
        _make_pilot_doc(app, uid_a, "private_license.pdf")
        _login(client, app, "pilot_b@a.com")
        # 404 not 403 — don't leak that the file exists
        assert client.get("/uploads/private_license.pdf").status_code == 404

    def test_user_from_other_tenant_gets_404(self, app, client):
        uid_a, _ = _make_tenant_and_user(app, "pa@a.com")
        _make_tenant_and_user(app, "pb@b.com")
        _write_file(app, "cross_tenant_license.pdf")
        _make_pilot_doc(app, uid_a, "cross_tenant_license.pdf")
        _login(client, app, "pb@b.com")
        assert client.get("/uploads/cross_tenant_license.pdf").status_code == 404


class TestFlightEntryPhotoAccess:
    def _make_flight_with_photo(self, app, tenant_id, field, filename):
        acid = _make_aircraft(app, tenant_id, reg=f"OO-{field[:3].upper()}")
        _write_file(app, filename)
        with app.app_context():
            kwargs: dict = {
                "aircraft_id": acid,
                "date": date(2024, 6, 1),
                "departure_icao": "EBBR",
                "arrival_icao": "EBOS",
                field: filename,
            }
            fe = FlightEntry(**kwargs)
            db.session.add(fe)
            db.session.commit()
        return acid

    def test_flight_counter_photo_owner_can_access(self, app, client):
        _, tid = _make_tenant_and_user(app, "fcp_owner@a.com")
        self._make_flight_with_photo(app, tid, "flight_counter_photo", "flt_ctr.jpg")
        _login(client, app, "fcp_owner@a.com")
        assert client.get("/uploads/flt_ctr.jpg").status_code == 200

    def test_engine_counter_photo_owner_can_access(self, app, client):
        _, tid = _make_tenant_and_user(app, "ecp_owner@a.com")
        self._make_flight_with_photo(app, tid, "engine_counter_photo", "eng_ctr.jpg")
        _login(client, app, "ecp_owner@a.com")
        assert client.get("/uploads/eng_ctr.jpg").status_code == 200

    def test_fuel_photo_owner_can_access(self, app, client):
        _, tid = _make_tenant_and_user(app, "fp_owner@a.com")
        self._make_flight_with_photo(app, tid, "fuel_photo", "fuel.jpg")
        _login(client, app, "fp_owner@a.com")
        assert client.get("/uploads/fuel.jpg").status_code == 200

    def test_flight_photo_other_tenant_gets_404(self, app, client):
        _, tid_a = _make_tenant_and_user(app, "fpa@a.com")
        _make_tenant_and_user(app, "fpb@b.com")
        self._make_flight_with_photo(
            app, tid_a, "flight_counter_photo", "other_flt.jpg"
        )
        _login(client, app, "fpb@b.com")
        assert client.get("/uploads/other_flt.jpg").status_code == 404


class TestUnknownFile:
    def test_unknown_filename_returns_404(self, app, client):
        _make_tenant_and_user(app, "nobody@a.com")
        _login(client, app, "nobody@a.com")
        assert client.get("/uploads/does_not_exist_anywhere.pdf").status_code == 404


class TestOrphanDocument:
    def test_document_with_no_owner_returns_404(self, app, client):
        """A Document with no owner FK (bad data) is refused rather than served."""
        _make_tenant_and_user(app, "orphan@a.com")
        _write_file(app, "orphan.pdf")
        with app.app_context():
            doc = Document(
                filename="orphan.pdf",
                original_filename="orphan.pdf",
                mime_type="application/pdf",
            )
            db.session.add(doc)
            db.session.commit()
        _login(client, app, "orphan@a.com")
        assert client.get("/uploads/orphan.pdf").status_code == 404
