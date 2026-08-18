"""
Tests for backlog item: standalone refuel record (not tied to any flight).
"""

from datetime import date

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Refuel,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_user_and_tenant(app, email="pilot@example.com", role=Role.ADMIN):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=_pw_hash.hash("testpassword123"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=role))
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="pilot@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _add_aircraft(app, tenant_id, registration="OO-TST"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration=registration, make="Cessna", model="172S"
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _login_orphan_user(app, client):
    with app.app_context():
        user = User(
            email="orphan@example.com",
            password_hash=_pw_hash.hash("x"),
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        uid = user.id
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def _add_refuel(app, aircraft_id, quantity=40.0, unit="L", note=None, refuel_date=None):
    with app.app_context():
        r = Refuel(
            aircraft_id=aircraft_id,
            date=refuel_date or date.today(),
            quantity=quantity,
            unit=unit,
            note=note,
        )
        db.session.add(r)
        db.session.commit()
        return r.id


# ── Refuel list ───────────────────────────────────────────────────────────────


class TestListRefuels:
    def test_redirects_when_not_logged_in(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        resp = client.get(f"/aircraft/{ac_id}/refuels")
        assert resp.status_code == 302

    def test_404_for_wrong_tenant(self, app, client):
        _, _t1 = _create_user_and_tenant(app, "a@example.com")
        _, t2 = _create_user_and_tenant(app, "b@example.com")
        ac_id = _add_aircraft(app, t2)
        _login(app, client, "a@example.com")
        resp = client.get(f"/aircraft/{ac_id}/refuels")
        assert resp.status_code == 404

    def test_shows_refuels(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_refuel(app, ac_id, quantity=42.5, note="Topped off before maintenance")
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/refuels")
        assert resp.status_code == 200
        assert b"42.5" in resp.data
        assert b"Topped off before maintenance" in resp.data

    def test_403_when_user_has_no_tenant(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login_orphan_user(app, client)
        resp = client.get(f"/aircraft/{ac_id}/refuels")
        assert resp.status_code == 403

    def test_empty_state(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/refuels")
        assert b"No refuels logged yet" in resp.data

    def test_log_as_expense_link_shown_for_owner(self, app, client):
        _, tenant_id = _create_user_and_tenant(app, role=Role.OWNER)
        ac_id = _add_aircraft(app, tenant_id)
        _add_refuel(app, ac_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/refuels")
        assert b"Log as expense" in resp.data

    def test_log_as_expense_link_hidden_for_non_owner(self, app, client):
        from models import UserAircraftAccess  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app, role=Role.PILOT)
        ac_id = _add_aircraft(app, tenant_id)
        with app.app_context():
            uid = User.query.filter_by(email="pilot@example.com").first().id
            db.session.add(UserAircraftAccess(user_id=uid, aircraft_id=ac_id))
            db.session.commit()
        _add_refuel(app, ac_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/refuels")
        assert b"Log as expense" not in resp.data


# ── New refuel ────────────────────────────────────────────────────────────────


class TestNewRefuel:
    def test_get_shows_form(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/refuels/new")
        assert resp.status_code == 200
        assert b"Log a refuel" in resp.data

    def test_post_creates_refuel(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/refuels/new",
            data={"date": "2026-01-05", "quantity": "35.5", "unit": "L", "note": "x"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            r = Refuel.query.filter_by(aircraft_id=ac_id).first()
            assert r is not None
            assert float(r.quantity) == 35.5
            assert r.unit == "L"
            assert r.date.isoformat() == "2026-01-05"

    def test_post_missing_date_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/refuels/new", data={"date": "", "quantity": "10"}
        )
        assert b"Date is required" in resp.data

    def test_post_invalid_date_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/refuels/new",
            data={"date": "not-a-date", "quantity": "10"},
        )
        assert b"must be a valid date" in resp.data

    def test_post_future_date_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/refuels/new",
            data={"date": "2999-01-01", "quantity": "10"},
        )
        assert b"cannot be in the future" in resp.data

    def test_post_missing_quantity_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/refuels/new",
            data={"date": "2026-01-05", "quantity": ""},
        )
        assert b"Quantity is required" in resp.data

    def test_post_negative_quantity_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/refuels/new",
            data={"date": "2026-01-05", "quantity": "-5"},
        )
        assert b"positive number" in resp.data

    def test_post_zero_quantity_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/refuels/new",
            data={"date": "2026-01-05", "quantity": "0"},
        )
        assert b"positive number" in resp.data

    def test_post_non_numeric_quantity_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/refuels/new",
            data={"date": "2026-01-05", "quantity": "abc"},
        )
        assert b"positive number" in resp.data

    def test_invalid_unit_falls_back_to_liters(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/refuels/new",
            data={"date": "2026-01-05", "quantity": "10", "unit": "bogus"},
        )
        with app.app_context():
            r = Refuel.query.filter_by(aircraft_id=ac_id).first()
            assert r.unit == "L"

    def test_redirects_when_not_logged_in(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        resp = client.post(
            f"/aircraft/{ac_id}/refuels/new",
            data={"date": "2026-01-05", "quantity": "10"},
        )
        assert resp.status_code == 302


# ── Edit refuel ───────────────────────────────────────────────────────────────


class TestEditRefuel:
    def test_get_shows_form(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        refuel_id = _add_refuel(app, ac_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/refuels/{refuel_id}/edit")
        assert resp.status_code == 200
        assert b"Edit Refuel" in resp.data

    def test_post_updates_refuel(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        refuel_id = _add_refuel(app, ac_id, quantity=10.0)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/refuels/{refuel_id}/edit",
            data={"date": "2026-02-01", "quantity": "55.0", "unit": "gal", "note": "n"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            r = db.session.get(Refuel, refuel_id)
            assert float(r.quantity) == 55.0
            assert r.unit == "gal"
            assert r.note == "n"
            assert r.date.isoformat() == "2026-02-01"

    def test_404_wrong_aircraft(self, app, client):
        _, t1 = _create_user_and_tenant(app, "a@example.com")
        _, t2 = _create_user_and_tenant(app, "b@example.com")
        ac1 = _add_aircraft(app, t1, "OO-A")
        ac2 = _add_aircraft(app, t2, "OO-B")
        refuel_id = _add_refuel(app, ac2)
        _login(app, client, "a@example.com")
        resp = client.get(f"/aircraft/{ac1}/refuels/{refuel_id}/edit")
        assert resp.status_code == 404


# ── Delete refuel ─────────────────────────────────────────────────────────────


class TestDeleteRefuel:
    def test_delete_removes_refuel(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        refuel_id = _add_refuel(app, ac_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/refuels/{refuel_id}/delete", follow_redirects=True
        )
        assert resp.status_code == 200
        with app.app_context():
            assert db.session.get(Refuel, refuel_id) is None

    def test_404_wrong_aircraft(self, app, client):
        _, t1 = _create_user_and_tenant(app, "a@example.com")
        _, t2 = _create_user_and_tenant(app, "b@example.com")
        ac1 = _add_aircraft(app, t1, "OO-A")
        ac2 = _add_aircraft(app, t2, "OO-B")
        refuel_id = _add_refuel(app, ac2)
        _login(app, client, "a@example.com")
        resp = client.post(f"/aircraft/{ac1}/refuels/{refuel_id}/delete")
        assert resp.status_code == 404


# ── Model ─────────────────────────────────────────────────────────────────────


class TestRefuelModel:
    def test_cascade_delete_refuels_with_aircraft(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        refuel_id = _add_refuel(app, ac_id)
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            db.session.delete(ac)
            db.session.commit()
            assert db.session.get(Refuel, refuel_id) is None


# ── Aircraft detail page integration ────────────────────────────────────────


class TestAircraftDetailRefuelsSection:
    def test_detail_shows_refuels_section_for_owner(self, app, client):
        _, tenant_id = _create_user_and_tenant(app, role=Role.OWNER)
        ac_id = _add_aircraft(app, tenant_id)
        _add_refuel(app, ac_id, quantity=12.3, note="Post-flight top-up")
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}")
        assert resp.status_code == 200
        assert b"12.3" in resp.data
        assert b"Post-flight top-up" in resp.data

    def test_detail_shows_empty_state_when_no_refuels(self, app, client):
        _, tenant_id = _create_user_and_tenant(app, role=Role.OWNER)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}")
        assert resp.status_code == 200
        assert b"No refuels logged yet" in resp.data
