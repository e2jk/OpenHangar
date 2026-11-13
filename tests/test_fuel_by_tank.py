"""
Tests for fuel remaining per tank, on aircraft with several tanks:
  - the flight form's per-tank inputs (total = their sum) and the flight page
  - keeping a per-tank split consistent when only the total is saved
  - importing it from an airframe logbook column like "9/13"
"""

from datetime import date
from decimal import Decimal

import pytest
from flights.airframe_import import (  # pyright: ignore[reportMissingImports]
    _apply_fuel_fields,
    _parse_litres_per_tank,
)
from flights.form_parsing import (  # pyright: ignore[reportMissingImports]
    reconcile_fuel_remaining_by_tank,
)
from models import (  # pyright: ignore[reportMissingImports]
    AircraftFuelTank,
    Flight,
    db,
)

from tests.test_airframe_import import _upload
from tests.test_flights import (
    _add_aircraft,
    _create_user_and_tenant,
    _login,
)

_TANKS = [("Left", 40.0), ("Right", 40.0)]


def _aircraft_with_tanks(app, tanks=_TANKS, registration="OO-TNK"):
    _uid, tid = _create_user_and_tenant(app)
    acid = _add_aircraft(app, tid, registration=registration)
    with app.app_context():
        for i, (name, cap) in enumerate(tanks):
            db.session.add(
                AircraftFuelTank(
                    aircraft_id=acid, name=name, capacity_liters=cap, sort_order=i
                )
            )
        db.session.commit()
    return acid


def _flight_form(acid, **kwargs):
    data = {
        "aircraft_id": str(acid),
        "date": "2026-11-02",
        "departure_icao": "EGMC",
        "arrival_icao": "EGHF",
        "flight_time_counter_start": "100.0",
        "flight_time_counter_end": "101.0",
        "crew_name_0": "Test Pilot",
        "crew_role_0": "PIC",
    }
    data.update(kwargs)
    return data


# ── Flight form & page ────────────────────────────────────────────────────────


class TestFlightForm:
    def test_per_tank_inputs_sum_into_the_total(self, app, client):
        acid = _aircraft_with_tanks(app)
        _login(app, client)
        page = client.get(f"/flights/new?aircraft_id={acid}").data.decode()
        assert 'name="fuel_remaining_tank_0"' in page
        assert 'name="fuel_remaining_tank_1"' in page
        assert 'data-target="fuel_remaining_tank_1"' in page

        client.post(
            "/flights/new",
            data=_flight_form(
                acid,
                fuel_remaining_qty="99",  # ignored: the tanks give the total
                fuel_remaining_tank_0="9",
                fuel_remaining_tank_1="13",
            ),
        )
        with app.app_context():
            flight = Flight.query.filter_by(aircraft_id=acid).one()
            assert flight.fuel_remaining_qty == Decimal(22)
            assert flight.fuel_remaining_by_tank == [
                {"tank": "Left", "liters": 9.0},
                {"tank": "Right", "liters": 13.0},
            ]
            flight_id = flight.id

        # The edit form prefills each tank; the flight page shows the split.
        page = client.get(f"/flights/{flight_id}/edit").data.decode()
        assert 'id="fuel_remaining_tank_1" name="fuel_remaining_tank_1"' in page
        tank1 = page.split('name="fuel_remaining_tank_1"', 1)[1].split(">", 1)[0]
        assert 'value="13.0"' in tank1
        page = client.get(f"/aircraft/{acid}/flights/{flight_id}").data.decode()
        assert "22.0 L" in page
        assert "Left 9.0 L · Right 13.0 L" in page

    def test_only_a_total_still_works(self, app, client):
        acid = _aircraft_with_tanks(app)
        _login(app, client)
        client.post("/flights/new", data=_flight_form(acid, fuel_remaining_qty="30"))
        with app.app_context():
            flight = Flight.query.filter_by(aircraft_id=acid).one()
            assert flight.fuel_remaining_qty == Decimal(30)
            assert flight.fuel_remaining_by_tank is None

    def test_invalid_tank_value_rejected(self, app, client):
        acid = _aircraft_with_tanks(app)
        _login(app, client)
        resp = client.post(
            "/flights/new", data=_flight_form(acid, fuel_remaining_tank_0="-1")
        )
        assert resp.status_code == 200
        assert (
            b"Fuel remaining in tank Left must be a non-negative number." in resp.data
        )
        with app.app_context():
            assert Flight.query.filter_by(aircraft_id=acid).count() == 0

    def test_single_tank_aircraft_has_no_per_tank_inputs(self, app, client):
        acid = _aircraft_with_tanks(app, tanks=[("Main", 110.0)])
        _login(app, client)
        page = client.get(f"/flights/new?aircraft_id={acid}").data.decode()
        assert "fuel_remaining_tank_0" not in page


class TestReconcile:
    SPLIT = ({"tank": "Left", "liters": 9.0}, {"tank": "Right", "liters": 13.0})

    def test_submitted_split_wins(self):
        new = [{"tank": "Left", "liters": 5.0}]
        assert reconcile_fuel_remaining_by_tank(list(self.SPLIT), 5.0, new) == new

    def test_split_kept_while_it_matches_the_total(self):
        existing = list(self.SPLIT)
        assert reconcile_fuel_remaining_by_tank(existing, 22.0, None) == existing

    @pytest.mark.parametrize("total", [25.0, None])
    def test_split_dropped_when_total_changes(self, total):
        assert reconcile_fuel_remaining_by_tank(list(self.SPLIT), total, None) is None

    def test_nothing_to_keep(self):
        assert reconcile_fuel_remaining_by_tank(None, 22.0, None) is None


# ── Import ────────────────────────────────────────────────────────────────────


class TestParseLitresPerTank:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("9/13", [9.0, 13.0]),
            (" 9 / 13 ", [9.0, 13.0]),
            ("9;13", [9.0, 13.0]),
            ("9,5 | 13", [9.5, 13.0]),
            ("22", [22.0]),
            (22, [22.0]),
            ("9/x", None),
            ("9/-1", None),
            ("", None),
            (None, None),
            (True, None),
        ],
    )
    def test_values(self, raw, expected):
        assert _parse_litres_per_tank(raw) == expected

    def test_apply_matches_tanks_in_order(self):
        fields = {"fuel_remaining_by_tank": [9.0, 13.0], "fuel_remaining_fraction": 0.5}
        assert _apply_fuel_fields(fields, {}, _TANKS) is None
        # Per-tank litres win over a fraction of the whole.
        assert fields == {
            "fuel_remaining_qty": 22.0,
            "fuel_remaining_by_tank": [
                {"tank": "Left", "liters": 9.0},
                {"tank": "Right", "liters": 13.0},
            ],
        }

    def test_apply_reports_a_count_mismatch(self):
        fields = {"fuel_remaining_by_tank": [9.0, 13.0, 4.0]}
        assert _apply_fuel_fields(fields, {}, _TANKS) == [9.0, 13.0, 4.0]
        assert fields == {}

    def test_apply_without_tanks_does_nothing(self):
        fields = {"fuel_remaining_by_tank": [9.0, 13.0]}
        assert _apply_fuel_fields(fields, {}, None) is None
        assert fields == {}


_CSV = (
    "Date,From,To,Duration,Landings,Fuel left\n"
    "2026-11-02,EGMC,EGHF,0.9,1,9/13\n"
    "2026-11-03,EGHF,EBAW,1.5,1,9/13/4\n"
)
_FORM = {
    "mapping_date": "date",
    "mapping_from": "departure_icao",
    "mapping_to": "arrival_icao",
    "mapping_duration": "flight_time",
    "mapping_landings": "landing_count",
    "mapping_fuel left": "fuel_remaining_by_tank",
}


class TestImportPerTank:
    def _import(self, client, acid):
        _upload(client, acid, csv_text=_CSV)
        client.post(f"/aircraft/{acid}/flights/import/execute", data=_FORM)
        with client.session_transaction() as sess:
            return [m for _c, m in sess.pop("_flashes", [])]

    def test_litres_per_tank_imported(self, app, client):
        acid = _aircraft_with_tanks(app)
        _login(app, client)
        flashes = self._import(client, acid)
        # The 3-value row can't be matched to 2 tanks: reported, not guessed.
        assert any("could not be parsed" in m and "9/13/4" in m for m in flashes)
        with app.app_context():
            by_date = {f.date: f for f in Flight.query.filter_by(aircraft_id=acid)}
            first = by_date[date(2026, 11, 2)]
            assert first.fuel_remaining_qty == Decimal(22)
            assert first.fuel_remaining_by_tank == [
                {"tank": "Left", "liters": 9.0},
                {"tank": "Right", "liters": 13.0},
            ]
            second = by_date[date(2026, 11, 3)]
            assert second.fuel_remaining_qty is None
            assert second.fuel_remaining_by_tank is None

    def test_needs_tanks_configured(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid, registration="OO-NTK")
        _login(app, client)
        flashes = self._import(client, acid)
        assert any("Fuel left per tank was not imported" in m for m in flashes)

    def test_duplicate_gets_the_split_too(self, app, client):
        acid = _aircraft_with_tanks(app, registration="OO-DUP")
        with app.app_context():
            db.session.add(
                Flight(
                    aircraft_id=acid,
                    date=date(2026, 11, 2),
                    departure_icao="EGMC",
                    arrival_icao="EGHF",
                    flight_time=Decimal("0.9"),
                    landing_count=1,
                )
            )
            db.session.commit()
        _login(app, client)
        flashes = self._import(client, acid)
        assert any("Fuel figures were added to one flight" in m for m in flashes)
        with app.app_context():
            flight = Flight.query.filter_by(
                aircraft_id=acid, date=date(2026, 11, 2)
            ).one()
            assert flight.fuel_remaining_qty == Decimal(22)
            assert flight.fuel_remaining_by_tank[1] == {"tank": "Right", "liters": 13.0}


class TestImportParseWarningsShown:
    """The airframe import reports cells it couldn't read (it used to drop
    them silently) — same message as the pilot logbook import."""

    def test_more_than_three_summarized(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid, registration="OO-PWS")
        _login(app, client)
        csv_text = (
            "Date,From,To,Duration,Landings\n"
            "2026-11-02,EBAW,EBAW,-,x\n"
            "2026-11-03,EBAW,EBAW,?,y\n"
        )
        _upload(client, acid, csv_text=csv_text)
        client.post(
            f"/aircraft/{acid}/flights/import/execute",
            data={
                "mapping_date": "date",
                "mapping_from": "departure_icao",
                "mapping_to": "arrival_icao",
                "mapping_duration": "flight_time",
                "mapping_landings": "landing_count",
            },
        )
        with client.session_transaction() as sess:
            flashes = [m for _c, m in sess.pop("_flashes", [])]
        assert any(
            m.startswith("4 cell values could not be parsed") and "… +1" in m
            for m in flashes
        )
