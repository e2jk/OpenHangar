"""
Tests for fuel tracking end to end:
  - importing fuel added (before/after the flight) and fuel left from an
    airframe logbook, including onto flights already in the log
  - the fuel log: refuels matched to their costs, and the period figures
  - the fuel log page and its "Add cost" round trip
"""

from datetime import date, time
from decimal import Decimal

import pytest
from flights.airframe_import import (  # pyright: ignore[reportMissingImports]
    _apply_fuel_fields,
    _fuel_added_before_flight,
    _parse_fuel_quantity,
    _parse_tank_fraction,
    aircraft_tank_capacity_l,
)
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AircraftFuelTank,
    Expense,
    ExpenseType,
    Flight,
    Refuel,
    db,
)
from refuels.fuel_log import (  # pyright: ignore[reportMissingImports]
    AFTER,
    BEFORE,
    STANDALONE,
    FuelEvent,
    build_fuel_log,
    summarize,
    to_litres,
)

from tests.test_airframe_import import (
    _add_aircraft,
    _create_user_and_tenant,
    _login,
    _upload,
)

# ── Parsing ───────────────────────────────────────────────────────────────────


class TestFuelParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (50, 50.0),
            (27.11, 27.11),
            ("34", 34.0),
            ("27,11 l", 27.11),
            ("42 L", 42.0),
            ("10 gal", 10.0),
            (0, None),
            ("", None),
            (None, None),
            ("full", None),
            (True, None),
        ],
    )
    def test_fuel_quantity(self, raw, expected):
        assert _parse_fuel_quantity(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (0.25, 0.25),
            (1, 1.0),
            (0, 0.0),
            ("3/8", 0.375),
            (" 6 / 8 ", 0.75),
            (3, 0.375),  # a count of eighths
            ("0,5", 0.5),
            ("9/8", None),
            ("1/0", None),
            (12, None),
            ("half", None),
            (None, None),
            (False, None),
        ],
    )
    def test_tank_fraction(self, raw, expected):
        assert _parse_tank_fraction(raw) == expected

    @pytest.mark.parametrize(
        ("timing", "before"),
        [
            ("FFBF", True),
            ("ffbf", True),
            ("Before flight", True),
            ("avant le vol", True),
            ("FFAF", False),
            ("FFBF+FFAF", False),  # both, only the total known
            ("To 6/8 AF (not full)", False),
            ("", False),
            (None, False),
            ("BFF", False),  # a marker inside a word doesn't count
        ],
    )
    def test_timing(self, timing, before):
        assert _fuel_added_before_flight(timing) is before


class TestApplyFuelFields:
    def test_quantity_defaults_to_after_the_flight(self):
        fields = {"fuel_added": 50.0}
        _apply_fuel_fields(fields, {"fuel (l)": "fuel_added"}, None)
        assert fields == {"fuel_added_after_qty": 50.0, "fuel_added_after_unit": "L"}

    def test_before_and_gallons(self):
        fields = {"fuel_added": 10.0, "fuel_timing": "FFBF"}
        _apply_fuel_fields(
            fields, {"fuel (gal)": "fuel_added", "refuel": "fuel_timing"}, None
        )
        assert fields == {
            "fuel_added_before_qty": 10.0,
            "fuel_added_before_unit": "gal",
        }

    def test_fraction_needs_tank_capacity(self):
        fields = {"fuel_remaining_fraction": 0.25}
        _apply_fuel_fields(fields, {}, None)
        assert fields == {}
        fields = {"fuel_remaining_fraction": 0.375}
        _apply_fuel_fields(fields, {}, 110.0)
        assert fields == {"fuel_remaining_qty": 41.2}

    def test_tank_capacity_sums_all_tanks(self, app):
        _uid, tid = _create_user_and_tenant(app, email="tanks@example.com")
        acid = _add_aircraft(app, tid, registration="OO-TNK")
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            assert aircraft_tank_capacity_l(ac) is None
            db.session.add(
                AircraftFuelTank(aircraft_id=acid, name="L", capacity_liters=55)
            )
            db.session.add(
                AircraftFuelTank(aircraft_id=acid, name="R", capacity_liters=55)
            )
            db.session.commit()
            assert aircraft_tank_capacity_l(ac) == 110.0


# ── Import ────────────────────────────────────────────────────────────────────

_FUEL_CSV = (
    "Date,From,To,Duration,Landings,Fuel (L),Refuel,Fuel left (8ths)\n"
    "2026-10-04,EBAW,LFAT,1.3,1,50,FFAF,0.25\n"
    "2026-10-14,EBAW,EBAW,1.4,2,49,FFBF,5/8\n"
    "2026-10-15,EBAW,EBAW,1.0,1,,,\n"
)
_FUEL_FORM = {
    "mapping_date": "date",
    "mapping_from": "departure_icao",
    "mapping_to": "arrival_icao",
    "mapping_duration": "flight_time",
    "mapping_landings": "landing_count",
    "mapping_fuel (l)": "fuel_added",
    "mapping_refuel": "fuel_timing",
    "mapping_fuel left (8ths)": "fuel_remaining_fraction",
}


def _import(client, acid, csv_text=_FUEL_CSV):
    _upload(client, acid, csv_text=csv_text)
    return client.post(f"/aircraft/{acid}/flights/import/execute", data=_FUEL_FORM)


def _flashes(client):
    with client.session_transaction() as sess:
        return [m for _c, m in sess.pop("_flashes", [])]


class TestFuelImport:
    def test_mapping_proposes_fuel_columns(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="fi0@example.com")
        acid = _add_aircraft(app, tid, registration="OO-FI0")
        _login(app, client, email="fi0@example.com")
        page = _upload(client, acid, csv_text=_FUEL_CSV).data.decode()
        for col, target in (
            ("fuel (l)", "fuel_added"),
            ("refuel", "fuel_timing"),
            ("fuel left (8ths)", "fuel_remaining_fraction"),
        ):
            select = page.split(f'name="mapping_{col}"', 1)[1].split("</select>", 1)[0]
            assert f'value="{target}" selected' in select

    def test_fuel_imported_and_fuel_left_needs_tanks(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="fi1@example.com")
        acid = _add_aircraft(app, tid, registration="OO-FI1")
        _login(app, client, email="fi1@example.com")
        _import(client, acid)
        assert any("no fuel tank capacity" in m for m in _flashes(client))
        with app.app_context():
            by_date = {f.date: f for f in Flight.query.filter_by(aircraft_id=acid)}
            oct4, oct14 = by_date[date(2026, 10, 4)], by_date[date(2026, 10, 14)]
            assert oct4.fuel_added_after_qty == Decimal(50)
            assert oct4.fuel_added_after_unit == "L"
            assert oct4.fuel_added_before_qty is None
            assert oct14.fuel_added_before_qty == Decimal(49)
            assert oct4.fuel_remaining_qty is None

            # Tanks configured afterwards: importing the file again fills the
            # fuel left on the flights already in the log, nothing else.
            db.session.add(
                AircraftFuelTank(aircraft_id=acid, name="Main", capacity_liters=110)
            )
            db.session.commit()

        _import(client, acid)
        flashes = _flashes(client)
        assert any("Fuel figures were added to 2 flights" in m for m in flashes)
        assert not any("no fuel tank capacity" in m for m in flashes)
        with app.app_context():
            flights = Flight.query.filter_by(aircraft_id=acid).all()
            assert len(flights) == 3
            by_date = {f.date: f for f in flights}
            assert by_date[date(2026, 10, 4)].fuel_remaining_qty == Decimal("27.5")
            assert by_date[date(2026, 10, 14)].fuel_remaining_qty == Decimal("68.8")
            assert by_date[date(2026, 10, 4)].fuel_added_after_qty == Decimal(50)

    def test_fuel_added_never_doubled_on_a_flight_that_has_some(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="fi2@example.com")
        acid = _add_aircraft(app, tid, registration="OO-FI2")
        with app.app_context():
            db.session.add(
                Flight(
                    aircraft_id=acid,
                    date=date(2026, 10, 4),
                    departure_icao="EBAW",
                    arrival_icao="LFAT",
                    flight_time=Decimal("1.3"),
                    landing_count=1,
                    fuel_added_before_qty=Decimal(50),
                    fuel_added_before_unit="L",
                    fuel_remaining_qty=Decimal(30),
                )
            )
            db.session.commit()
        _login(app, client, email="fi2@example.com")
        _import(
            client,
            acid,
            csv_text=_FUEL_CSV.splitlines()[0]
            + "\n"
            + _FUEL_CSV.splitlines()[1]
            + "\n",
        )
        assert not any("Fuel figures were added" in m for m in _flashes(client))
        with app.app_context():
            flight = Flight.query.filter_by(aircraft_id=acid).one()
            assert flight.fuel_added_before_qty == Decimal(50)
            assert flight.fuel_added_after_qty is None
            assert flight.fuel_remaining_qty == Decimal(30)

    def test_row_repeated_within_the_file_imported_once(self, app, client):
        """The repeat is a duplicate of a row not saved yet — no flight to
        fill, and the flight is imported once with its fuel."""
        _uid, tid = _create_user_and_tenant(app, email="fi5@example.com")
        acid = _add_aircraft(app, tid, registration="OO-FI5")
        _login(app, client, email="fi5@example.com")
        row = _FUEL_CSV.splitlines()[1]
        _import(client, acid, csv_text=_FUEL_CSV.splitlines()[0] + f"\n{row}\n{row}\n")
        assert not any("Fuel figures were added" in m for m in _flashes(client))
        with app.app_context():
            flight = Flight.query.filter_by(aircraft_id=acid).one()
            assert flight.fuel_added_after_qty == 50

    def test_duplicate_without_fuel_columns_changes_nothing(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="fi3@example.com")
        acid = _add_aircraft(app, tid, registration="OO-FI3")
        _login(app, client, email="fi3@example.com")
        csv_text = "Date,From,To,Duration,Landings\n2026-10-04,EBAW,LFAT,1.3,1\n"
        form = {
            k: v for k, v in _FUEL_FORM.items() if "fuel" not in k and "refuel" not in k
        }
        for _ in range(2):
            _upload(client, acid, csv_text=csv_text)
            client.post(f"/aircraft/{acid}/flights/import/execute", data=form)
        assert not any("Fuel figures were added" in m for m in _flashes(client))

    def test_duplicate_on_another_flight_of_the_day_not_filled(self, app, client):
        """A same-day flight with another route isn't the row's flight."""
        _uid, tid = _create_user_and_tenant(app, email="fi4@example.com")
        acid = _add_aircraft(app, tid, registration="OO-FI4")
        _login(app, client, email="fi4@example.com")
        header = _FUEL_CSV.splitlines()[0]
        _import(client, acid, csv_text=header + "\n2026-10-04,EBAW,LFAT,1.3,1,,,\n")
        with app.app_context():
            db.session.add(
                Flight(
                    aircraft_id=acid,
                    date=date(2026, 10, 4),
                    departure_icao="LFAT",
                    arrival_icao="EBAW",
                    flight_time=Decimal("1.3"),
                    landing_count=1,
                )
            )
            db.session.commit()
        _import(client, acid, csv_text=header + "\n2026-10-04,EBAW,LFAT,1.3,1,50,,\n")
        with app.app_context():
            by_route = {
                (f.departure_icao, f.arrival_icao): f
                for f in Flight.query.filter_by(aircraft_id=acid)
            }
            assert by_route[("EBAW", "LFAT")].fuel_added_after_qty == Decimal(50)
            assert by_route[("LFAT", "EBAW")].fuel_added_after_qty is None


# ── Fuel log ──────────────────────────────────────────────────────────────────


def _aircraft(app, email):
    _uid, tid = _create_user_and_tenant(app, email=email)
    return _add_aircraft(app, tid, registration="OO-" + email[:3].upper())


def _flight(acid, day, **kwargs):
    defaults = {
        "aircraft_id": acid,
        "date": day,
        "departure_icao": "EBAW",
        "arrival_icao": "EBAW",
        "flight_time": Decimal("1.0"),
        "engine_time": Decimal("1.2"),
    }
    defaults.update(kwargs)
    flight = Flight(**defaults)
    db.session.add(flight)
    db.session.flush()
    return flight


def _fuel_expense(
    acid, day, amount, quantity=None, unit="L", flight=None, currency="EUR"
):
    expense = Expense(
        aircraft_id=acid,
        date=day,
        expense_type=ExpenseType.FUEL,
        amount=Decimal(str(amount)),
        currency=currency,
        quantity=None if quantity is None else Decimal(str(quantity)),
        unit=unit,
        flight_entry_id=flight.id if flight else None,
    )
    db.session.add(expense)
    db.session.flush()
    return expense


class TestBuildFuelLog:
    def test_events_matched_to_their_costs(self, app):
        acid = _aircraft(app, "fl1@example.com")
        with app.app_context():
            d = date(2026, 10, 4)
            both = _flight(
                acid,
                d,
                departure_time=time(8, 0),
                fuel_added_before_qty=Decimal(20),
                fuel_added_before_unit="L",
                fuel_added_after_qty=Decimal(50),
                fuel_added_after_unit="L",
            )
            uncosted = _flight(
                acid,
                date(2026, 10, 5),
                fuel_added_after_qty=Decimal(10),
                fuel_added_after_unit="gal",
            )
            standalone = Refuel(
                aircraft_id=acid, date=d, quantity=Decimal(30), unit="L", note="maint"
            )
            db.session.add(standalone)
            db.session.add(
                Refuel(aircraft_id=acid, date=d, quantity=Decimal(5), unit="L")
            )
            # The "after" refuel's expense (same quantity) wins over the order.
            after_cost = _fuel_expense(acid, d, 100, quantity=50, flight=both)
            before_cost = _fuel_expense(acid, d, 41, quantity=None, flight=both)
            standalone_cost = _fuel_expense(acid, d, 60, quantity=30)
            # Not a fuel expense: never matched.
            db.session.add(
                Expense(
                    aircraft_id=acid,
                    date=d,
                    expense_type=ExpenseType.OTHER,
                    amount=Decimal(9),
                    quantity=Decimal(5),
                )
            )
            db.session.commit()

            events = build_fuel_log(acid, date(2026, 1, 1), date(2026, 12, 31))
            assert [(e.date, e.kind) for e in events][:1] == [
                (date(2026, 10, 5), AFTER)
            ]
            by_kind = {(e.kind, e.quantity): e for e in events}
            assert by_kind[(AFTER, 50.0)].expense.id == after_cost.id
            assert by_kind[(BEFORE, 20.0)].expense.id == before_cost.id
            assert by_kind[(STANDALONE, 30.0)].expense.id == standalone_cost.id
            assert by_kind[(STANDALONE, 5.0)].expense is None
            assert by_kind[(AFTER, 10.0)].expense is None
            assert by_kind[(AFTER, 10.0)].flight.id == uncosted.id
            assert by_kind[(STANDALONE, 30.0)].refuel.id == standalone.id
            assert by_kind[(AFTER, 50.0)].price_per_litre == 2.0
            assert by_kind[(STANDALONE, 5.0)].price_per_litre is None
            # Same day: the flight's "after" refuel is listed above its "before".
            same_day = [e.kind for e in events if e.date == d and e.flight is not None]
            assert same_day == [AFTER, BEFORE]

    def test_period_limits_events(self, app):
        acid = _aircraft(app, "fl2@example.com")
        with app.app_context():
            _flight(acid, date(2025, 1, 1), fuel_added_after_qty=Decimal(40))
            db.session.add(
                Refuel(
                    aircraft_id=acid,
                    date=date(2025, 1, 2),
                    quantity=Decimal(9),
                    unit="L",
                )
            )
            _flight(acid, date(2026, 6, 1), fuel_added_after_qty=Decimal(50))
            _flight(acid, date(2026, 6, 2))  # no fuel: not a refuel
            db.session.commit()
            assert [
                e.quantity
                for e in build_fuel_log(acid, date(2026, 1, 1), date(2026, 12, 31))
            ] == [50.0]
            assert len(build_fuel_log(acid, None, date(2026, 12, 31))) == 3


class TestSummarize:
    def _event(self, quantity, unit="L", expense=None):
        return FuelEvent(date(2026, 1, 1), AFTER, quantity, unit, expense=expense)

    class _Expense:
        def __init__(self, amount, quantity=None, unit="L", currency="EUR"):
            self.amount, self.quantity, self.unit, self.currency = (
                amount,
                quantity,
                unit,
                currency,
            )

    class _Flight:
        def __init__(self, engine=None, airborne=None, counters=None):
            self.engine_time = engine
            self.flight_time = airborne
            start, end = counters or (None, None)
            self.engine_time_counter_start = start
            self.engine_time_counter_end = end
            self.flight_time_counter_start = None
            self.flight_time_counter_end = None

    def test_figures(self):
        fuel = self._Expense(100, quantity=50)
        events = [self._event(50, expense=fuel), self._event(10, unit="gal")]
        flights = [
            self._Flight(engine=Decimal("2.0"), airborne=Decimal("1.6")),
            self._Flight(
                counters=(Decimal("10.0"), Decimal("11.0")), airborne=Decimal("0.9")
            ),
            self._Flight(),  # nothing logged: not a flight for the per-flight cost
        ]
        s = summarize(events, [fuel, self._Expense(20)], flights)
        assert s.refuel_count == 2
        assert s.costed_count == 1
        assert s.litres_added == pytest.approx(50 + to_litres(10, "gal"))
        assert (s.currency, s.cost) == ("EUR", 120.0)
        assert s.price_per_litre == 2.0  # only expenses with a quantity
        assert (s.engine_hours, s.flight_hours, s.flight_count) == (3.0, 2.5, 2)
        assert s.cost_per_engine_hour == 40.0
        assert s.cost_per_flight_hour == 48.0
        assert s.cost_per_flight == 60.0
        assert s.litres_per_engine_hour == pytest.approx(s.litres_added / 3.0)

    def test_several_currencies_give_no_ratios(self):
        s = summarize(
            [],
            [self._Expense(100, 50), self._Expense(80, 40, currency="GBP")],
            [self._Flight(engine=1)],
        )
        assert s.cost_by_currency == {"EUR": 100.0, "GBP": 80.0}
        assert s.cost is None and s.price_per_litre is None
        assert s.cost_per_engine_hour is None

    def test_nothing_logged(self):
        s = summarize([], [], [])
        assert s.cost is None and s.litres_per_engine_hour is None


# ── Fuel log page ─────────────────────────────────────────────────────────────


class TestFuelLogPage:
    def _setup(self, app, client, email):
        acid = _aircraft(app, email)
        _login(app, client, email=email)
        today = date.today()
        with app.app_context():
            flight = _flight(
                acid,
                today,
                fuel_added_after_qty=Decimal(40),
                fuel_added_after_unit="L",
            )
            costed = _flight(
                acid,
                today,
                departure_icao="EBNM",
                fuel_added_before_qty=Decimal(20),
                fuel_added_before_unit="L",
            )
            _fuel_expense(acid, today, 44, quantity=20, flight=costed)
            refuel = Refuel(
                aircraft_id=acid,
                date=today,
                quantity=Decimal(15),
                unit="L",
                note="hangar",
            )
            db.session.add(refuel)
            db.session.commit()
            return acid, flight.id, refuel.id

    def test_lists_refuels_with_costs_and_figures(self, app, client):
        acid, flight_id, refuel_id = self._setup(app, client, "fp1@example.com")
        page = client.get(f"/aircraft/{acid}/refuels").data.decode()
        assert "Fuel log" in page
        assert "75 L" in page  # 40 + 20 + 15
        assert "1 of 3 refuels costed" in page
        assert "44.00 EUR" in page and "2.20 EUR/L" in page
        assert f"flight_entry_id={flight_id}" in page and "return_to=fuel" in page
        assert f"/refuels/{refuel_id}/edit" in page
        assert "hangar" in page

    def test_period_selector(self, app, client):
        acid, _fid, _rid = self._setup(app, client, "fp2@example.com")
        assert (
            "Fuel log" in client.get(f"/aircraft/{acid}/refuels?period=6").data.decode()
        )
        # Unknown or invalid periods fall back to the default.
        for period in ("7", "x"):
            page = client.get(f"/aircraft/{acid}/refuels?period={period}").data.decode()
            assert 'value="12" selected' in page

    def test_empty_period(self, app, client):
        acid = _aircraft(app, "fp3@example.com")
        _login(app, client, email="fp3@example.com")
        page = client.get(f"/aircraft/{acid}/refuels").data.decode()
        assert "No refuels in this period." in page

    def test_add_cost_returns_to_fuel_log(self, app, client):
        acid, flight_id, _rid = self._setup(app, client, "fp4@example.com")
        url = (
            f"/aircraft/{acid}/expenses/add?flight_entry_id={flight_id}"
            "&quantity=40&unit=L&return_to=fuel"
        )
        rv = client.post(
            url,
            data={
                "date": date.today().isoformat(),
                "expense_type": "fuel",
                "amount": "88.00",
                "currency": "EUR",
                "quantity": "40",
                "unit": "L",
                "flight_entry_id": str(flight_id),
            },
        )
        assert rv.headers["Location"].endswith("/aircraft/OO-FP4/refuels")
        page = client.get(f"/aircraft/{acid}/refuels").data.decode()
        assert "2 of 3 refuels costed" in page
