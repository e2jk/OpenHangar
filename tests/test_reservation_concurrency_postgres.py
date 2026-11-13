"""Real-connection reservation races in an isolated PostgreSQL schema.

Set TEST_POSTGRES_URL to a disposable PostgreSQL database. Every
run creates and drops its own schema; no application/operator tables are used.
"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event, local
from time import monotonic, sleep
from typing import Any

import pytest
from flask import Flask
from models import (
    Aircraft,
    DispatchRecord,
    Flight,
    RentalCharge,
    Reservation,
    ReservationStatus,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)
from sqlalchemy import text

from tests.postgres_schema import postgres_app

Booking = tuple[int, int, list[int]]
Operation = tuple[str, dict[str, str]]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)


@pytest.fixture
def booking(app: Flask) -> Booking:
    with app.app_context():
        tenant = Tenant(name="Disposable concurrency test")
        user = User(email="owner@example.test", password_hash="unused", is_active=True)
        db.session.add_all([tenant, user])
        db.session.flush()
        db.session.add(
            TenantUser(tenant_id=tenant.id, user_id=user.id, role=Role.OWNER)
        )
        ac = Aircraft(
            tenant_id=tenant.id, registration="TEST", make="Test", model="Test"
        )
        db.session.add(ac)
        db.session.flush()
        start = datetime(2027, 1, 1, 10, tzinfo=UTC)
        reservations = [
            Reservation(
                aircraft_id=ac.id,
                pilot_user_id=user.id,
                start_dt=start,
                end_dt=start + timedelta(hours=1),
                status=ReservationStatus.PENDING,
            )
            for _ in range(2)
        ]
        db.session.add_all(reservations)
        db.session.commit()
        return user.id, ac.id, [r.id for r in reservations]


def race(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    booking: Booking,
    operations: list[Operation],
) -> list[tuple[int, list[str]]]:
    """Hold writer 0 at aircraft lookup until PostgreSQL proves writer 1 waits.

    Both real connections rendezvous before lookup. An external connection
    observes the blocked FOR UPDATE, not a timing assumption or mocked lock.
    """
    from reservations import routes

    uid, acid, _ = booking
    clients = [app.test_client(), app.test_client()]
    for client in clients:
        with client.session_transaction() as sess:
            sess["user_id"] = uid
    barrier = Barrier(2, timeout=10)
    first_locked, second_started, release_first = Event(), Event(), Event()
    second_loaded = Event()
    worker = local()
    original = routes._get_aircraft_or_404
    pids = {}
    with app.app_context():
        engine = db.engine

    def synchronized_lookup(aircraft_id: int, **kwargs: Any) -> Aircraft:
        i = worker.index
        pids[i] = db.session.execute(text("SELECT pg_backend_pid()")).scalar_one()
        # Deliberately retain stale ORM state across the wait. The route must
        # refresh both the reservation and its dispatch relationship after locking.
        worker.cached = [db.session.get(Reservation, rid) for rid in booking[2]]
        worker.cached_dispatch = [reservation.dispatch for reservation in worker.cached]
        barrier.wait()
        if i == 1:
            assert first_locked.wait(10)
            second_started.set()
        ac = original(aircraft_id, **kwargs)
        if i == 0:
            first_locked.set()
            assert release_first.wait(10)
        else:
            second_loaded.set()
        return ac

    monkeypatch.setattr(routes, "_get_aircraft_or_404", synchronized_lookup)

    def post(i: int) -> tuple[int, list[str]]:
        worker.index = i
        path, data = operations[i]
        response = clients[i].post(f"/aircraft/{acid}/" + path, data=data)
        with clients[i].session_transaction() as sess:
            messages = [str(message) for _, message in sess.get("_flashes", [])]
        return response.status_code, messages

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(post, i) for i in range(2)]
        try:
            assert second_started.wait(10)
            assert len(set(pids.values())) == 2
            deadline = monotonic() + 5
            with engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as observer:
                while monotonic() < deadline:
                    blocked = observer.execute(
                        text(
                            "SELECT :first = ANY(pg_blocking_pids(pid)) "
                            "AND wait_event_type = 'Lock' AND query LIKE 'SELECT aircraft.%' FROM pg_stat_activity WHERE pid = :second"
                        ),
                        {"first": pids[0], "second": pids[1]},
                    ).scalar()
                    if blocked:
                        break
                    assert not second_loaded.is_set(), (
                        "second writer bypassed aircraft lock"
                    )
                    sleep(0.01)
                else:
                    pytest.fail(
                        "PostgreSQL did not observe competing aircraft-row locks: "
                        + repr(
                            observer.execute(
                                text(
                                    "SELECT pid, pg_blocking_pids(pid), wait_event_type, query FROM pg_stat_activity WHERE pid IN (:first, :second)"
                                ),
                                {"first": pids[0], "second": pids[1]},
                            ).all()
                        )
                    )
        finally:
            release_first.set()
        results = [future.result(timeout=20) for future in futures]
    return results


@pytest.mark.parametrize("adjacent", [False, True])
def test_competing_confirmations(
    app: Flask, monkeypatch: pytest.MonkeyPatch, booking: Booking, adjacent: bool
) -> None:
    _, _, ids = booking
    if adjacent:
        with app.app_context():
            r = db.session.get(Reservation, ids[1])
            r.start_dt += timedelta(hours=1)
            r.end_dt += timedelta(hours=1)
            db.session.commit()
    results = race(
        app, monkeypatch, booking, [(f"reservations/{rid}/confirm", {}) for rid in ids]
    )
    assert [status for status, _ in results] == [302, 302]
    if not adjacent:
        assert any("Cannot confirm: overlaps" in message for message in results[1][1])
    with app.app_context():
        assert Reservation.query.filter_by(
            status=ReservationStatus.CONFIRMED
        ).count() == (2 if adjacent else 1)
        assert Reservation.query.filter_by(
            status=ReservationStatus.PENDING
        ).count() == (0 if adjacent else 1)


@pytest.mark.parametrize("stage", ["checkout", "checkin"])
def test_duplicate_dispatch(
    app: Flask, monkeypatch: pytest.MonkeyPatch, booking: Booking, stage: str
) -> None:
    uid, _, ids = booking
    with app.app_context():
        r = db.session.get(Reservation, ids[0])
        r.status = ReservationStatus.CONFIRMED
        if stage == "checkin":
            db.session.add(
                DispatchRecord(
                    reservation_id=r.id,
                    out_at=datetime.now(UTC),
                    out_by_id=uid,
                    out_engine_counter=100,
                )
            )
        db.session.commit()
    data = {
        "walkaround_ok": "1",
        "snags_acknowledged": "1",
        "out_engine_counter": "100",
        "in_engine_counter": "101",
    }
    results = race(
        app, monkeypatch, booking, [(f"reservations/{ids[0]}/{stage}", data)] * 2
    )
    assert [status for status, _ in results] == [302, 302]
    assert any(
        f"already been checked {'out' if stage == 'checkout' else 'in'}" in message
        for message in results[1][1]
    )
    with app.app_context():
        assert DispatchRecord.query.count() == 1
        dispatch = DispatchRecord.query.one()
        assert dispatch.out_engine_counter == 100
        assert dispatch.is_checked_in == (stage == "checkin")
        assert RentalCharge.query.count() == (1 if stage == "checkin" else 0)
        # Dispatch currently creates no flight; repeated requests must not change that.
        assert Flight.query.count() == 0


@pytest.mark.parametrize(
    "downtime_first,override", [(True, False), (False, False), (False, True)]
)
def test_downtime_and_confirmation(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    booking: Booking,
    downtime_first: bool,
    override: bool,
) -> None:
    from models import MaintenanceDowntime

    _, _, ids = booking
    downtime = (
        "downtimes/new",
        {
            "start_dt": "2027-01-01T10:00",
            "end_dt": "2027-01-01T11:00",
            "confirm_conflicts": "1" if override else "",
        },
    )
    confirmation = (f"reservations/{ids[0]}/confirm", {})
    operations = (
        [downtime, confirmation] if downtime_first else [confirmation, downtime]
    )
    results = race(app, monkeypatch, booking, operations)
    assert results[0][0] == 302
    assert results[1][0] == (302 if downtime_first or override else 200)
    with app.app_context():
        assert db.session.get(Reservation, ids[0]).status == (
            ReservationStatus.PENDING if downtime_first else ReservationStatus.CONFIRMED
        )
        assert MaintenanceDowntime.query.count() == (
            1 if downtime_first or override else 0
        )


@pytest.mark.parametrize("operation", ["edit", "delete"])
def test_downtime_changes_serialize_with_confirmation(
    app: Flask, monkeypatch: pytest.MonkeyPatch, booking: Booking, operation: str
) -> None:
    from models import MaintenanceDowntime

    uid, acid, ids = booking
    with app.app_context():
        d = MaintenanceDowntime(
            aircraft_id=acid,
            created_by_id=uid,
            start_dt=datetime(2027, 1, 1, 10, tzinfo=UTC),
            end_dt=datetime(2027, 1, 1, 11, tzinfo=UTC),
        )
        db.session.add(d)
        db.session.commit()
        did = d.id
    results = race(
        app,
        monkeypatch,
        booking,
        [
            (
                f"downtimes/{did}/{operation}",
                {"start_dt": "2027-01-01T12:00", "end_dt": "2027-01-01T13:00"},
            ),
            (f"reservations/{ids[0]}/confirm", {}),
        ],
    )
    assert [status for status, _ in results] == [302, 302]
    with app.app_context():
        assert db.session.get(Reservation, ids[0]).status == ReservationStatus.CONFIRMED
        assert MaintenanceDowntime.query.count() == (1 if operation == "edit" else 0)


def test_owner_edit_is_visible_to_competing_confirmation(
    app, monkeypatch, booking
) -> None:
    _, _, ids = booking
    with app.app_context():
        r = db.session.get(Reservation, ids[0])
        r.status = ReservationStatus.CONFIRMED
        r.start_dt -= timedelta(hours=1)
        r.end_dt -= timedelta(hours=1)
        db.session.commit()
    results = race(
        app,
        monkeypatch,
        booking,
        [
            (
                f"reservations/{ids[0]}/edit",
                {"start_dt": "2027-01-01T10:00", "end_dt": "2027-01-01T11:00"},
            ),
            (f"reservations/{ids[1]}/confirm", {}),
        ],
    )
    assert [status for status, _ in results] == [302, 302]
    with app.app_context():
        assert db.session.get(Reservation, ids[1]).status == ReservationStatus.PENDING


@pytest.mark.parametrize("checkout_first", [True, False])
def test_cancellation_and_checkout(
    app: Flask, monkeypatch: pytest.MonkeyPatch, booking: Booking, checkout_first: bool
) -> None:
    _, _, ids = booking
    with app.app_context():
        db.session.get(Reservation, ids[0]).status = ReservationStatus.CONFIRMED
        db.session.commit()
    checkout = (
        f"reservations/{ids[0]}/checkout",
        {"walkaround_ok": "1", "snags_acknowledged": "1"},
    )
    cancel = (f"reservations/{ids[0]}/cancel", {})
    results = race(
        app,
        monkeypatch,
        booking,
        [checkout, cancel] if checkout_first else [cancel, checkout],
    )
    assert [status for status, _ in results] == [302, 302]
    with app.app_context():
        assert db.session.get(Reservation, ids[0]).status == (
            ReservationStatus.CONFIRMED
            if checkout_first
            else ReservationStatus.CANCELLED
        )
        assert DispatchRecord.query.count() == (1 if checkout_first else 0)
        assert RentalCharge.query.count() == 0


def test_validation_failure_releases_lock_for_confirmation(
    app, monkeypatch, booking
) -> None:
    _, _, ids = booking
    results = race(
        app,
        monkeypatch,
        booking,
        [
            ("reservations/new", {"start_dt": "invalid", "end_dt": "invalid"}),
            (f"reservations/{ids[0]}/confirm", {}),
        ],
    )
    assert [status for status, _ in results] == [200, 302]
    with app.app_context():
        assert Reservation.query.count() == 2
        assert db.session.get(Reservation, ids[0]).status == ReservationStatus.CONFIRMED
