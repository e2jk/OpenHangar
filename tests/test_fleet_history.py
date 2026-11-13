"""The fleet booking overview's bounded history (audit P03).

Overlap detection and the missing-flight check must give exactly what the
previous every-pair / per-reservation code gave, without work that grows
with an aircraft's lifetime of bookings.
"""

import random
from datetime import UTC, date, datetime, timedelta
from itertools import combinations
from types import SimpleNamespace

from models import Flight, Reservation, ReservationStatus, db
from reservations.routes import (
    _overlapping_reservation_ids,
    _past_reservations_without_flight,
)
from sqlalchemy import event

from tests.test_reservations import _login, _make_aircraft, _make_user

T0 = datetime(2026, 3, 1, 8, 0)


def _r(rid, start_h, end_h, aircraft=1):
    return SimpleNamespace(
        id=rid,
        aircraft_id=aircraft,
        start_dt=T0 + timedelta(hours=start_h),
        end_dt=T0 + timedelta(hours=end_h),
    )


def _every_pair(reservations):
    ids = set()
    for a, b in combinations(reservations, 2):
        if (
            a.aircraft_id == b.aircraft_id
            and a.start_dt < b.end_dt
            and a.end_dt > b.start_dt
        ):
            ids.update((a.id, b.id))
    return ids


class TestOverlaps:
    def test_adjacent_bookings_do_not_overlap(self):
        assert _overlapping_reservation_ids([_r(1, 0, 2), _r(2, 2, 4)]) == set()

    def test_nested_same_start_and_chains(self):
        cases = {
            "nested": ([_r(1, 0, 10), _r(2, 3, 4)], {1, 2}),
            "same start": ([_r(1, 0, 2), _r(2, 0, 5)], {1, 2}),
            "chain": ([_r(1, 0, 3), _r(2, 2, 5), _r(3, 4, 7), _r(4, 8, 9)], {1, 2, 3}),
            "one long over many": (
                [_r(1, 0, 20), _r(2, 1, 2), _r(3, 5, 6), _r(4, 30, 31)],
                {1, 2, 3},
            ),
            "other aircraft": ([_r(1, 0, 2), _r(2, 1, 3, aircraft=2)], set()),
        }
        for name, (bookings, expected) in cases.items():
            assert _overlapping_reservation_ids(bookings) == expected, name

    def test_same_result_as_comparing_every_pair(self):
        rng = random.Random(20261113)
        for _ in range(300):
            bookings = []
            for rid in range(rng.randint(0, 12)):
                start = rng.randint(0, 40)
                bookings.append(
                    _r(rid, start, start + rng.randint(1, 8), rng.randint(1, 2))
                )
            assert _overlapping_reservation_ids(list(bookings)) == _every_pair(bookings)


class TestMissingFlights:
    def _flight(self, app, aircraft_id, day):
        with app.app_context():
            db.session.add(
                Flight(
                    aircraft_id=aircraft_id,
                    date=day,
                    departure_icao="EBAW",
                    arrival_icao="EBKT",
                )
            )
            db.session.commit()

    def test_flight_on_any_day_of_the_booking_counts(self, app):
        _, tid = _make_user(app, "missing@fleet.test")
        ac = _make_aircraft(app, tid, "OO-MIS")
        other = _make_aircraft(app, tid, "OO-OTH")
        self._flight(app, ac, date(2026, 3, 3))  # last day of booking 1
        self._flight(app, other, date(2026, 3, 10))  # other aircraft: no help
        bookings = [
            SimpleNamespace(
                id=1,
                aircraft_id=ac,
                start_dt=datetime(2026, 3, 1, 9),
                end_dt=datetime(2026, 3, 3, 18),
            ),
            SimpleNamespace(
                id=2,
                aircraft_id=ac,
                start_dt=datetime(2026, 3, 10, 9),
                end_dt=datetime(2026, 3, 10, 11),
            ),
            SimpleNamespace(
                id=3,
                aircraft_id=ac,
                start_dt=datetime(2026, 3, 4, 9),
                end_dt=datetime(2026, 3, 4, 11),
            ),
        ]
        with app.app_context():
            assert _past_reservations_without_flight(bookings) == {2, 3}
            assert _past_reservations_without_flight([]) == set()

    def test_same_result_as_one_query_per_booking(self, app):
        _, tid = _make_user(app, "random@fleet.test")
        aircraft = [_make_aircraft(app, tid, f"OO-R{i}") for i in range(2)]
        rng = random.Random(7)
        for _ in range(15):
            self._flight(
                app,
                rng.choice(aircraft),
                date(2026, 1, 1) + timedelta(days=rng.randint(0, 60)),
            )
        bookings = []
        for rid in range(40):
            start = datetime(2026, 1, 1, 9) + timedelta(days=rng.randint(0, 60))
            bookings.append(
                SimpleNamespace(
                    id=rid,
                    aircraft_id=rng.choice(aircraft),
                    start_dt=start,
                    end_dt=start + timedelta(days=rng.randint(0, 3), hours=2),
                )
            )
        with app.app_context():
            expected = {
                b.id
                for b in bookings
                if Flight.query.filter(
                    Flight.aircraft_id == b.aircraft_id,
                    Flight.date >= b.start_dt.date(),
                    Flight.date <= b.end_dt.date(),
                ).first()
                is None
            }
            assert _past_reservations_without_flight(bookings) == expected


class TestFleetPage:
    def _book(self, app, ac, uid, days_ago, hours=2):
        start = datetime.now(UTC) - timedelta(days=days_ago)
        with app.app_context():
            db.session.add(
                Reservation(
                    aircraft_id=ac,
                    pilot_user_id=uid,
                    start_dt=start,
                    end_dt=start + timedelta(hours=hours),
                    status=ReservationStatus.CONFIRMED,
                )
            )
            db.session.commit()

    def test_older_history_is_behind_an_explicit_link(self, app, client):
        uid, tid = _make_user(app, "history@fleet.test")
        ac = _make_aircraft(app, tid, "OO-HIS")
        self._book(app, ac, uid, days_ago=10)
        self._book(app, ac, uid, days_ago=200)
        _login(app, client, uid)
        page = client.get("/reservations/fleet/").data.decode()
        assert "Show one older reservation" in page
        everything = client.get("/reservations/fleet/?history=all").data.decode()
        assert "Show only the last 90 days" in everything
        assert len(everything) > len(page)

    def test_query_count_does_not_grow_with_past_bookings(self, app, client):
        uid, tid = _make_user(app, "queries@fleet.test")
        ac = _make_aircraft(app, tid, "OO-QRY")
        _login(app, client, uid)

        def statements_for_page():
            seen = []
            with app.app_context():
                engine = db.engine
            listener = lambda *args: seen.append(1)
            event.listen(engine, "before_cursor_execute", listener)
            try:
                assert client.get("/reservations/fleet/").status_code == 200
            finally:
                event.remove(engine, "before_cursor_execute", listener)
            return len(seen)

        for day in range(1, 4):
            self._book(app, ac, uid, days_ago=day)
        few = statements_for_page()
        for day in range(4, 40):
            self._book(app, ac, uid, days_ago=day)
        many = statements_for_page()
        assert many == few, (few, many)
