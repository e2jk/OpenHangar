"""
Tests for EE-03 — Milestone Confetti.

Covers _check_flight_hour_milestone and the list_flights route behaviour
when a session milestone flag is set.
"""

import bcrypt  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    FlightEntry,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _setup(app, email="pilot@example.com"):
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
        ac = Aircraft(
            tenant_id=tenant.id, registration="OO-EE3", make="Cessna", model="172"
        )
        db.session.add(ac)
        db.session.commit()
        return user.id, tenant.id, ac.id


def _login(client, uid):
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def _add_flight_entry(app, aircraft_id, flight_time):
    with app.app_context():
        fe = FlightEntry(
            aircraft_id=aircraft_id,
            date=__import__("datetime").date(2024, 1, 1),
            departure_icao="EBOS",
            arrival_icao="EBBR",
            flight_time=flight_time,
        )
        db.session.add(fe)
        db.session.commit()
        return fe.id


def _post_flight(client, aircraft_id, ft_start, ft_end):
    return client.post(
        "/flights/new",
        data={
            "aircraft_id": str(aircraft_id),
            "date": "2024-06-01",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "crew_name_0": "Test Pilot",
            "crew_role_0": "PIC",
            "flight_time_counter_start": str(ft_start),
            "flight_time_counter_end": str(ft_end),
        },
        follow_redirects=False,
    )


# ── _check_flight_hour_milestone ──────────────────────────────────────────────


class TestCheckFlightHourMilestone:
    def test_skips_when_flight_time_is_none(self, app, client):
        uid, tid, acid = _setup(app)
        _login(client, uid)
        # Flight with no flight_time set → milestone check is a no-op
        with app.app_context():
            fe = FlightEntry(
                aircraft_id=acid,
                date=__import__("datetime").date(2024, 1, 1),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                flight_time=None,
            )
            db.session.add(fe)
            db.session.commit()

        # Invoke the helper directly inside a request context
        resp = client.get(f"/aircraft/{acid}/flights")
        assert resp.status_code == 200
        with client.session_transaction() as sess:
            assert "milestone_hours" not in sess

    def test_no_milestone_when_below_threshold(self, app, client):
        uid, tid, acid = _setup(app)
        _login(client, uid)
        # Add 50h of existing flights, then add a 3h flight → total 53h, no milestone
        _add_flight_entry(app, acid, 50.0)
        resp = _post_flight(client, acid, 200.0, 203.0)
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert "milestone_hours" not in sess

    def test_milestone_100h_sets_session_flag(self, app, client):
        uid, tid, acid = _setup(app)
        _login(client, uid)
        # 98h existing; add 3h flight → 101h total, crosses 100h
        _add_flight_entry(app, acid, 98.0)
        resp = _post_flight(client, acid, 200.0, 203.0)
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("milestone_hours") == 100

    def test_milestone_100h_flashes_message(self, app, client):
        uid, tid, acid = _setup(app)
        _login(client, uid)
        _add_flight_entry(app, acid, 98.0)
        resp = _post_flight(client, acid, 200.0, 203.0)
        # Follow redirect to see flash message
        list_resp = client.get(resp.headers["Location"])
        assert b"100" in list_resp.data

    def test_milestone_500h(self, app, client):
        uid, tid, acid = _setup(app)
        _login(client, uid)
        _add_flight_entry(app, acid, 498.0)
        resp = _post_flight(client, acid, 200.0, 203.0)
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("milestone_hours") == 500

    def test_only_first_milestone_crossed_is_flagged(self, app, client):
        uid, tid, acid = _setup(app)
        _login(client, uid)
        # Jump straight from 0h to 600h (crosses 100h and 500h); only 100h flagged
        resp = _post_flight(client, acid, 0.0, 600.0)
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("milestone_hours") == 100

    def test_no_milestone_when_flight_time_is_none(self, app, client):
        uid, tid, acid = _setup(app)
        _login(client, uid)
        # POST a flight with no counter data → flight_time is None → helper returns early
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert "milestone_hours" not in sess


# ── list_flights route ────────────────────────────────────────────────────────


class TestListFlightsMilestone:
    def test_milestone_hours_consumed_from_session(self, app, client):
        uid, tid, acid = _setup(app)
        _login(client, uid)
        with client.session_transaction() as sess:
            sess["milestone_hours"] = 100
        resp = client.get(f"/aircraft/{acid}/flights")
        assert resp.status_code == 200
        # The session flag is popped on this request
        with client.session_transaction() as sess:
            assert "milestone_hours" not in sess

    def test_confetti_script_present_when_milestone_set(self, app, client):
        uid, tid, acid = _setup(app)
        _login(client, uid)
        with client.session_transaction() as sess:
            sess["milestone_hours"] = 1000
        resp = client.get(f"/aircraft/{acid}/flights")
        assert b"canvas-confetti" in resp.data

    def test_confetti_script_absent_without_milestone(self, app, client):
        uid, tid, acid = _setup(app)
        _login(client, uid)
        resp = client.get(f"/aircraft/{acid}/flights")
        assert b"canvas-confetti" not in resp.data
