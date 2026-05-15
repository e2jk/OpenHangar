"""
Tests for counter properties and aircraft logbook settings.

Verifies that:
- total_engine_hours uses engine time counter
- total_flight_hours uses flight time counter
- MaintenanceTrigger.status() uses engine hours correctly
- Aircraft settings (regime, has_flight_counter, flight_counter_offset) persist
"""

from datetime import date

import bcrypt  # pyright: ignore[reportMissingImports]

from models import (
    Aircraft,
    FlightEntry,
    MaintenanceTrigger,
    Role,
    Tenant,
    TenantUser,
    TriggerType,
    User,
    db,
)  # pyright: ignore[reportMissingImports]


def _create_user_and_tenant(app, email="test@example.com"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
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


def _login(app, client, email="test@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


class TestCounterProperties:
    def test_total_engine_hours_uses_engine_counter(self, app):
        with app.app_context():
            tenant = Tenant(name="T1")
            db.session.add(tenant)
            db.session.flush()
            ac = Aircraft(
                tenant_id=tenant.id, registration="OO-T1", make="X", model="X"
            )
            db.session.add(ac)
            db.session.flush()
            db.session.add(
                FlightEntry(
                    aircraft_id=ac.id,
                    date=date(2024, 1, 1),
                    departure_icao="EBOS",
                    arrival_icao="EBBR",
                    flight_time_counter_start=100.0,
                    flight_time_counter_end=101.5,
                    engine_time_counter_start=500.0,
                    engine_time_counter_end=501.3,
                )
            )
            db.session.commit()
            ac = db.session.get(Aircraft, ac.id)
            assert ac.total_engine_hours == 501.3
            assert ac.total_flight_hours == 101.5

    def test_total_engine_hours_none_when_no_engine_counter(self, app):
        with app.app_context():
            tenant = Tenant(name="T2")
            db.session.add(tenant)
            db.session.flush()
            ac = Aircraft(
                tenant_id=tenant.id, registration="OO-T2", make="X", model="X"
            )
            db.session.add(ac)
            db.session.flush()
            db.session.add(
                FlightEntry(
                    aircraft_id=ac.id,
                    date=date(2024, 1, 1),
                    departure_icao="EBOS",
                    arrival_icao="EBBR",
                    flight_time_counter_start=100.0,
                    flight_time_counter_end=101.5,
                )
            )
            db.session.commit()
            ac = db.session.get(Aircraft, ac.id)
            assert ac.total_engine_hours is None
            assert ac.total_flight_hours == 101.5

    def test_total_flight_hours_none_when_no_flights(self, app):
        with app.app_context():
            tenant = Tenant(name="T3")
            db.session.add(tenant)
            db.session.flush()
            ac = Aircraft(
                tenant_id=tenant.id, registration="OO-T3", make="X", model="X"
            )
            db.session.add(ac)
            db.session.commit()
            ac = db.session.get(Aircraft, ac.id)
            assert ac.total_engine_hours is None
            assert ac.total_flight_hours is None


class TestMaintenanceTriggerUsesEngineHours:
    def test_status_uses_engine_hours(self, app):
        with app.app_context():
            tenant = Tenant(name="T4")
            db.session.add(tenant)
            db.session.flush()
            ac = Aircraft(
                tenant_id=tenant.id, registration="OO-T4", make="X", model="X"
            )
            db.session.add(ac)
            db.session.flush()
            t = MaintenanceTrigger(
                aircraft_id=ac.id,
                name="Test trigger",
                trigger_type=TriggerType.HOURS,
                due_engine_hours=510.0,
                interval_hours=50,
            )
            db.session.add(t)
            db.session.commit()
            assert t.status(current_hobbs=509.0) == "due_soon"
            assert t.status(current_hobbs=511.0) == "overdue"
            assert t.status(current_hobbs=450.0) == "ok"


class TestAircraftSettings:
    def test_defaults(self, app):
        with app.app_context():
            tenant = Tenant(name="T5")
            db.session.add(tenant)
            db.session.flush()
            ac = Aircraft(
                tenant_id=tenant.id, registration="OO-T5", make="X", model="X"
            )
            db.session.add(ac)
            db.session.commit()
            ac = db.session.get(Aircraft, ac.id)
            assert ac.regime == "EASA"
            assert ac.has_flight_counter is True
            assert float(ac.flight_counter_offset) == 0.3

    def test_settings_persist_via_form(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            ac = Aircraft(tenant_id=tid, registration="OO-T6", make="X", model="X")
            db.session.add(ac)
            db.session.commit()
            acid = ac.id
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/edit",
            data={
                "registration": "OO-T6",
                "make": "X",
                "model": "X",
                "regime": "FAA",
                "flight_counter_offset": "0.5",
            },
        )
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            assert ac.regime == "FAA"
            assert ac.has_flight_counter is False
            assert float(ac.flight_counter_offset) == 0.5

    def test_negative_flight_counter_offset_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app, email="pilot2@example.com")
        with app.app_context():
            ac = Aircraft(tenant_id=tid, registration="OO-T7", make="X", model="X")
            db.session.add(ac)
            db.session.commit()
            acid = ac.id
        _login(app, client, email="pilot2@example.com")
        resp = client.post(
            f"/aircraft/{acid}/edit",
            data={
                "registration": "OO-T7",
                "make": "X",
                "model": "X",
                "flight_counter_offset": "-1.0",
            },
        )
        assert resp.status_code == 200
        assert b"non-negative" in resp.data
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            assert float(ac.flight_counter_offset) == 0.3  # unchanged

    def test_invalid_flight_counter_offset_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app, email="pilot3@example.com")
        with app.app_context():
            ac = Aircraft(tenant_id=tid, registration="OO-T8", make="X", model="X")
            db.session.add(ac)
            db.session.commit()
            acid = ac.id
        _login(app, client, email="pilot3@example.com")
        resp = client.post(
            f"/aircraft/{acid}/edit",
            data={
                "registration": "OO-T8",
                "make": "X",
                "model": "X",
                "flight_counter_offset": "notanumber",
            },
        )
        assert resp.status_code == 200
        assert b"non-negative" in resp.data
