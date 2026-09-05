"""
Tests for backlog item: aggregate flight-statistics view.
"""

import io
from datetime import date
from unittest.mock import patch

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Flight,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)
from reports.flight_stats import (  # pyright: ignore[reportMissingImports]
    compute_flight_stats,
)
from utils import haversine_nm  # pyright: ignore[reportMissingImports]

# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_user_and_tenant(app, email="pilot@example.com"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(email=email, password_hash=_pw_hash.hash("pw"), is_active=True)
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="pilot@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _flight(**kwargs) -> Flight:
    defaults: dict = {
        "date": date(2024, 6, 1),
        "departure_icao": "EBOS",
        "arrival_icao": "EBBR",
        "flight_time": 1.0,
    }
    defaults.update(kwargs)
    return Flight(**defaults)


# ── haversine_nm ──────────────────────────────────────────────────────────────


class TestHaversineNm:
    def test_same_point_is_zero(self):
        assert haversine_nm(50.0, 4.0, 50.0, 4.0) == 0.0

    def test_one_degree_longitude_at_equator(self):
        # 1° of longitude at the equator is ~60.04 nm (Earth's circumference
        # / 360, in nautical miles by construction of the nm unit itself).
        distance = haversine_nm(0.0, 0.0, 0.0, 1.0)
        assert 59.9 < distance < 60.1


# ── _load_airport_geo ─────────────────────────────────────────────────────────


class TestLoadAirportGeo:
    def test_oserror_returns_empty_dict(self):
        from utils import _load_airport_geo  # pyright: ignore[reportMissingImports]

        _load_airport_geo.cache_clear()
        with patch("builtins.open", side_effect=OSError("no file")):
            result = _load_airport_geo()
        _load_airport_geo.cache_clear()
        assert result == {}

    def test_skips_rows_missing_ident_or_coords_or_with_bad_floats(self):
        from utils import _load_airport_geo  # pyright: ignore[reportMissingImports]

        csv_content = (
            "ident,latitude_deg,longitude_deg,elevation_ft,iso_country,iso_region,name\n"
            ",10.0,20.0,100,BE,BE-X,No Ident\n"
            "ZZZZ,,20.0,100,BE,BE-X,No Latitude\n"
            "ZZZY,notanumber,20.0,100,BE,BE-X,Bad Latitude\n"
            "EBOS,51.198899,2.862222,13,BE,BE-VWV,Ostend-Bruges\n"
        )
        _load_airport_geo.cache_clear()
        with patch("builtins.open", return_value=io.StringIO(csv_content)):
            result = _load_airport_geo()
        _load_airport_geo.cache_clear()
        assert set(result.keys()) == {"EBOS"}
        assert result["EBOS"]["elevation_ft"] == 13.0


# ── compute_flight_stats ──────────────────────────────────────────────────────


class TestComputeFlightStats:
    def test_empty_list(self):
        stats = compute_flight_stats([])
        assert stats["flight_count"] == 0
        assert stats["total_hours"] == 0.0
        assert stats["total_distance_nm"] == 0.0
        assert stats["airport_count"] == 0
        assert stats["farthest_north"] is None
        assert stats["highest_field"] is None
        assert stats["longest_leg"] is None
        assert stats["longest_day"] is None

    def test_basic_two_known_airports(self):
        f = _flight(departure_icao="EBOS", arrival_icao="EBBR", flight_time=1.0)
        stats = compute_flight_stats([f])
        assert stats["flight_count"] == 1
        assert stats["total_hours"] == 1.0
        assert stats["airport_count"] == 2
        assert stats["total_distance_nm"] > 0
        assert stats["longest_leg"]["dep"] == "EBOS"
        assert stats["longest_leg"]["arr"] == "EBBR"
        assert stats["longest_leg"]["distance_nm"] == stats["total_distance_nm"]

    def test_unresolvable_airport_codes_still_count_flight_and_hours(self):
        f = _flight(
            departure_icao="ZZZZ", arrival_icao="ZZZY", flight_time=2.5
        )  # not real ICAO idents
        stats = compute_flight_stats([f])
        assert stats["flight_count"] == 1
        assert stats["total_hours"] == 2.5
        assert stats["total_distance_nm"] == 0.0
        assert stats["airport_count"] == 0
        assert stats["longest_leg"] is None

    def test_falls_back_to_counter_delta_when_flight_time_missing(self):
        f = _flight(
            flight_time=None,
            flight_time_counter_start=100.0,
            flight_time_counter_end=101.5,
        )
        stats = compute_flight_stats([f])
        assert stats["total_hours"] == 1.5

    def test_zero_hours_when_neither_flight_time_nor_counters_set(self):
        f = _flight(flight_time=None)
        stats = compute_flight_stats([f])
        assert stats["total_hours"] == 0.0

    def test_farthest_corners_across_three_airports(self):
        # EBBR (Brussels, ~50.9N) / EBOS (Ostend, ~51.2N) / LFPG (Paris CDG, ~49.0N)
        flights = [
            _flight(departure_icao="EBBR", arrival_icao="EBOS"),
            _flight(departure_icao="EBBR", arrival_icao="LFPG"),
        ]
        stats = compute_flight_stats(flights)
        assert stats["farthest_north"]["icao"] == "EBOS"
        assert stats["farthest_south"]["icao"] == "LFPG"

    def test_highest_and_lowest_field(self):
        # LSGG (Geneva, ~1411 ft) is well above EBOS (Ostend, ~13 ft).
        flights = [_flight(departure_icao="EBOS", arrival_icao="LSGG")]
        stats = compute_flight_stats(flights)
        assert stats["highest_field"]["icao"] == "LSGG"
        assert stats["lowest_field"]["icao"] == "EBOS"

    def test_longest_day_sums_same_date_flights(self):
        busy_day = date(2024, 7, 1)
        quiet_day = date(2024, 7, 2)
        flights = [
            _flight(date=busy_day, departure_icao="EBOS", arrival_icao="EBBR"),
            _flight(date=busy_day, departure_icao="EBBR", arrival_icao="EBOS"),
            _flight(date=quiet_day, departure_icao="EBOS", arrival_icao="EBBR"),
        ]
        stats = compute_flight_stats(flights)
        assert stats["longest_day"]["date"] == busy_day
        assert stats["longest_day"]["flight_count"] == 2
        # Two legs vs. one on the quiet day — busy day's total must be larger.
        single_leg_distance = compute_flight_stats([flights[2]])["total_distance_nm"]
        assert stats["longest_day"]["distance_nm"] > single_leg_distance


# ── /pilot/stats route ────────────────────────────────────────────────────────


class TestPilotStatsRoute:
    def test_requires_login(self, client):
        resp = client.get("/pilot/stats", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_empty_state(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/pilot/stats")
        assert resp.status_code == 200
        assert b"No logged flights yet" in resp.data

    def test_shows_stats_with_flights(self, app, client):
        uid, _tid = _create_user_and_tenant(app)
        with app.app_context():
            db.session.add(
                Flight(
                    pic_user_id=uid,
                    date=date(2024, 6, 1),
                    departure_icao="EBOS",
                    arrival_icao="EBBR",
                    other_aircraft_type="C172S",
                    other_aircraft_registration="OO-TST",
                    single_pilot_se=1.5,
                    function_pic=1.5,
                )
            )
            db.session.commit()
        _login(app, client)
        resp = client.get("/pilot/stats")
        assert resp.status_code == 200
        assert b"EBOS" in resp.data
