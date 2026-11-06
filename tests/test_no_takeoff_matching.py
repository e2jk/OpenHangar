"""
Tests for matching an engine run that never took off (a flight cancelled
after engine start) across the pilot and airframe logbooks:
  - the pure predicates in services/no_takeoff_matching.py
  - the airframe import merging such a row into the pilot-logged flight
  - the pilot import merging such a row into the airframe-logged flight
  - re-importing either file afterwards adding nothing
"""

from datetime import date, time
from decimal import Decimal
from io import BytesIO

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
import pytest
from flights.airframe_import import (  # pyright: ignore[reportMissingImports]
    _is_no_takeoff_match as airframe_no_takeoff_match,
)
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Flight,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)
from pilots.logbook_import import (  # pyright: ignore[reportMissingImports]
    _is_no_takeoff_match as pilot_no_takeoff_match,
)
from services.no_takeoff_matching import (  # pyright: ignore[reportMissingImports]
    AirframeSide,
    PilotSide,
    airframe_never_airborne,
    is_no_takeoff_pair,
    pilot_no_landings,
)

# ── Pure predicates ───────────────────────────────────────────────────────────


def _af(**overrides):
    values = {
        "departure_icao": "EBAW",
        "arrival_icao": "EBAW",
        "takeoff_time": None,
        "landing_time": None,
        "flight_time": Decimal("0.0"),
        "landing_count": 0,
        "engine_time": Decimal("0.7"),
    }
    values.update(overrides)
    return AirframeSide(**values)


def _pl(**overrides):
    values = {
        "departure_icao": "EBAW",
        "arrival_icao": "EBAW",
        "departure_time": None,
        "arrival_time": None,
        "block_time": 0.7,
        "landings_day": 0,
        "landings_night": None,
    }
    values.update(overrides)
    return PilotSide(**values)


class TestPredicates:
    def test_cancelled_engine_run_pairs(self):
        assert is_no_takeoff_pair(_af(), _pl())

    def test_rounding_step_tolerated(self):
        assert is_no_takeoff_pair(_af(engine_time=0.8), _pl(block_time=0.7))
        assert not is_no_takeoff_pair(_af(engine_time=0.9), _pl(block_time=0.7))

    def test_route_compared_case_insensitively(self):
        assert is_no_takeoff_pair(_af(), _pl(departure_icao=" ebaw "))

    @pytest.mark.parametrize(
        "overrides",
        [
            {"takeoff_time": time(10, 0)},
            {"landing_time": time(10, 30)},
            # Airborne time not recorded is not evidence of "never flew".
            {"flight_time": None},
            {"flight_time": Decimal("0.4")},
            {"landing_count": 1},
            {"engine_time": None},
            {"engine_time": Decimal("0.0")},
        ],
    )
    def test_airframe_side_must_show_an_engine_run_without_flight(self, overrides):
        assert not airframe_never_airborne(_af(**overrides))
        assert not is_no_takeoff_pair(_af(**overrides), _pl())

    @pytest.mark.parametrize(
        "overrides",
        [
            {"departure_time": time(10, 0)},
            {"arrival_time": time(10, 42)},
            {"landings_day": 1},
            {"landings_night": 1},
            {"block_time": None},
            {"block_time": 0.0},
        ],
    )
    def test_pilot_side_must_show_block_time_without_landings(self, overrides):
        assert not pilot_no_landings(_pl(**overrides))
        assert not is_no_takeoff_pair(_af(), _pl(**overrides))

    @pytest.mark.parametrize(
        ("af", "pl"),
        [
            ({}, {"arrival_icao": "EBNM"}),
            ({"departure_icao": "ZZZZ"}, {"departure_icao": "ZZZZ"}),
            ({"arrival_icao": None}, {"arrival_icao": None}),
        ],
    )
    def test_route_must_be_known_and_equal(self, af, pl):
        assert not is_no_takeoff_pair(_af(**af), _pl(**pl))


# ── Import-level fixtures ─────────────────────────────────────────────────────


def _world(app, email):
    """An owner/pilot with one managed aircraft (OO-LKN)."""
    with app.app_context():
        tenant = Tenant(name="No-takeoff Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(email=email, password_hash=_pw_hash.hash("pw"), is_active=True)
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        ac = Aircraft(
            tenant_id=tenant.id,
            registration="OO-LKN",
            make="Robin",
            model="DR401",
            flight_counter_offset=Decimal("0.3"),
        )
        db.session.add(ac)
        db.session.commit()
        return user.id, ac.id


_DAY = date(2026, 10, 24)


def _pilot_logged(acid, uid, **overrides):
    """The cancelled flight as promoted from a personal-logbook import."""
    fields = {
        "aircraft_id": acid,
        "date": _DAY,
        "departure_icao": "EBAW",
        "arrival_icao": "EBAW",
        "single_pilot_se": Decimal("0.7"),
        "function_pic": Decimal("0.7"),
        "engine_time": Decimal("0.7"),
        "landings_day": 0,
        "pic_user_id": uid,
        "pic_name": "Klein",
        "notes": "ADM: cancelled, too much crosswind",
        "source": "logbook_import",
    }
    fields.update(overrides)
    fe = Flight(**fields)
    db.session.add(fe)
    db.session.commit()
    return fe


def _airframe_logged(acid, **overrides):
    """The cancelled flight as logged from the airframe side."""
    fields = {
        "aircraft_id": acid,
        "date": _DAY,
        "departure_icao": "EBAW",
        "arrival_icao": "EBAW",
        "flight_time": Decimal("0.0"),
        "flight_time_counter_start": Decimal("1555.1"),
        "flight_time_counter_end": Decimal("1555.1"),
        "engine_time_counter_start": Decimal("1972.1"),
        "engine_time_counter_end": Decimal("1972.8"),
        "engine_time": Decimal("0.7"),
        "landing_count": 0,
        "pic_name": "KLEIN",
        "nature_of_flight": "CANCEL FLIGHT",
    }
    fields.update(overrides)
    fe = Flight(**fields)
    db.session.add(fe)
    db.session.commit()
    return fe


_AIRFRAME_CSV = (
    "Date,Pilot,From,To,Dep,Arr,Duration,Estart,Eend,Fstart,Fend,Ldg,Nature\n"
    "2026-10-24,KLEIN,EBAW,EBAW,[empty],[empty],0:00,1972.1,1972.8,"
    "1555.1,1555.1,0,CANCEL FLIGHT\n"
)
_AIRFRAME_FORM = {
    "mapping_date": "date",
    "mapping_pilot": "crew_name",
    "mapping_from": "departure_icao",
    "mapping_to": "arrival_icao",
    "mapping_dep": "takeoff_time",
    "mapping_arr": "landing_time",
    "mapping_duration": "flight_time",
    "mapping_estart": "engine_counter_start",
    "mapping_eend": "engine_counter_end",
    "mapping_fstart": "flight_counter_start",
    "mapping_fend": "flight_counter_end",
    "mapping_ldg": "landing_count",
    "mapping_nature": "nature_of_flight",
}

_PILOT_CSV = (
    "Date,Reg,From,Dep,To,Arr,SE,PIC,Ldg,Remarks\n"
    "24/10/26,OO-LKN,EBAW,[empty],EBAW,[empty],0:42,0:42,0,"
    "ADM: cancelled - too much crosswind\n"
)
_PILOT_FORM = {
    "mapping_date": "date",
    "mapping_reg": "aircraft_registration",
    "mapping_from": "departure_place",
    "mapping_dep": "departure_time",
    "mapping_to": "arrival_place",
    "mapping_arr": "arrival_time",
    "mapping_se": "single_pilot_se",
    "mapping_pic": "function_pic",
    "mapping_ldg": "landings_day",
    "mapping_remarks": "remarks",
}


def _import_airframe(client, acid):
    client.post(
        f"/aircraft/{acid}/flights/import",
        data={"logbook_file": (BytesIO(_AIRFRAME_CSV.encode()), "af.csv", "text/csv")},
        content_type="multipart/form-data",
    )
    return client.post(f"/aircraft/{acid}/flights/import/execute", data=_AIRFRAME_FORM)


def _import_pilot(client):
    client.post(
        "/pilot/logbook/import",
        data={"logbook_file": (BytesIO(_PILOT_CSV.encode()), "log.csv")},
        content_type="multipart/form-data",
    )
    return client.post("/pilot/logbook/import/execute", data=_PILOT_FORM)


def _flashes(client):
    with client.session_transaction() as sess:
        return [msg for _cat, msg in sess.pop("_flashes", [])]


# ── Airframe import onto a pilot-logged cancelled flight ──────────────────────


class TestAirframeImport:
    def test_merged_into_pilot_logged_flight_and_reimport_adds_nothing(
        self, app, client
    ):
        uid, acid = _world(app, "nt-af@example.com")
        with app.app_context():
            pilot_id = _pilot_logged(acid, uid).id
        with client.session_transaction() as sess:
            sess["user_id"] = uid

        rv = _import_airframe(client, acid)
        assert "review" not in rv.headers["Location"]
        assert any(
            "One row matched a flight already in a pilot logbook" in m
            for m in _flashes(client)
        )
        with app.app_context():
            assert Flight.query.filter_by(aircraft_id=acid).count() == 1
            fe = db.session.get(Flight, pilot_id)
            assert fe.flight_time == Decimal("0.0")
            assert fe.engine_time_counter_start == Decimal("1972.1")
            assert fe.engine_time_counter_end == Decimal("1972.8")
            assert fe.landing_count == 0
            assert fe.nature_of_flight == "CANCEL FLIGHT"
            assert fe.single_pilot_se == Decimal("0.7")
            assert fe.pic_user_id == uid

        rv = _import_airframe(client, acid)
        assert "review" not in rv.headers["Location"]
        with app.app_context():
            assert Flight.query.filter_by(aircraft_id=acid).count() == 1

    def test_engine_time_mismatch_not_merged(self, app, client):
        uid, acid = _world(app, "nt-af2@example.com")
        with app.app_context():
            _pilot_logged(acid, uid, single_pilot_se=Decimal("1.5"))
        with client.session_transaction() as sess:
            sess["user_id"] = uid

        _import_airframe(client, acid)
        with app.app_context():
            assert Flight.query.filter_by(aircraft_id=acid).count() == 2

    def test_only_pilot_only_rows_considered(self, app):
        _uid, acid = _world(app, "nt-af3@example.com")
        with app.app_context():
            existing = _airframe_logged(acid)
            fields = {
                "departure_icao": "EBAW",
                "arrival_icao": "EBAW",
                "flight_time": 0.0,
                "landing_count": 0,
                "engine_time": 0.7,
            }
            assert not airframe_no_takeoff_match(fields, existing)


# ── Pilot import onto an airframe-logged cancelled flight ─────────────────────


class TestPilotImport:
    def test_merged_into_airframe_logged_flight_and_reimport_adds_nothing(
        self, app, client
    ):
        uid, acid = _world(app, "nt-pl@example.com")
        with app.app_context():
            airframe_id = _airframe_logged(acid).id
        with client.session_transaction() as sess:
            sess["user_id"] = uid
            sess["pilot_access"] = True

        rv = _import_pilot(client)
        assert rv.headers["Location"].endswith("/pilot/logbook/import/history")
        flashes = _flashes(client)
        assert any(
            "One row matched a flight already in the aircraft log" in m for m in flashes
        )
        # The "[empty]" block times are blanks, not unparseable values.
        assert not any("could not be parsed" in m for m in flashes)
        with app.app_context():
            assert Flight.query.filter_by(aircraft_id=acid).count() == 1
            fe = db.session.get(Flight, airframe_id)
            assert fe.pic_user_id == uid
            assert fe.single_pilot_se == Decimal("0.7")
            assert fe.function_pic == Decimal("0.7")
            assert fe.departure_time is None
            assert fe.flight_time == Decimal("0.0")
            assert fe.engine_time == Decimal("0.7")

        rv = _import_pilot(client)
        assert rv.headers["Location"].endswith("/pilot/logbook/import/history")
        with app.app_context():
            assert Flight.query.filter_by(aircraft_id=acid).count() == 1

    def test_other_registration_not_merged(self, app):
        _uid, acid = _world(app, "nt-pl2@example.com")
        with app.app_context():
            existing = _airframe_logged(acid)
            kwargs = {
                "other_aircraft_registration": "OO-ABC",
                "departure_icao": "EBAW",
                "arrival_icao": "EBAW",
                "single_pilot_se": 0.7,
                "landings_day": 0,
            }
            assert not pilot_no_takeoff_match(kwargs, existing)
            kwargs["other_aircraft_registration"] = "oo-lkn"
            assert pilot_no_takeoff_match(kwargs, existing)
            kwargs["other_aircraft_registration"] = None
            assert not pilot_no_takeoff_match(kwargs, existing)

    def test_only_airframe_only_rows_considered(self, app):
        uid, acid = _world(app, "nt-pl3@example.com")
        with app.app_context():
            existing = _pilot_logged(acid, uid, flight_time=Decimal("0.0"))
            kwargs = {
                "other_aircraft_registration": "OO-LKN",
                "departure_icao": "EBAW",
                "arrival_icao": "EBAW",
                "single_pilot_se": 0.7,
                "landings_day": 0,
            }
            assert not pilot_no_takeoff_match(kwargs, existing)
