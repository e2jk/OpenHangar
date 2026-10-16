"""
Tests for reconciling the pilot logbook and the airframe logbook of the same
real-world flight across their two imports:
  - a row whose only match is the same flight logged from the *other* side
    is merged automatically (fill-only, never overwriting)
  - the review step's "overwrite" never clears data the file doesn't carry,
    and never puts free-text aircraft fields on a managed-aircraft row
  - the pilot's block time fills a managed aircraft's engine_time
"""

from datetime import date, time
from decimal import Decimal
from io import BytesIO

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from flights.airframe_import import (  # pyright: ignore[reportMissingImports]
    AirframeConflictRow,
    merge_airframe_into_existing,
    split_airframe_auto_merges,
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
    ConflictRow,
    _fill_engine_time,
    merge_into_existing,
    split_auto_merges,
)


def _world(app, email):
    """A pilot/owner with one managed aircraft (OO-LKN, 0.3 h offset)."""
    with app.app_context():
        tenant = Tenant(name="Merge Hangar")
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


def _airframe_row(acid, **overrides):
    """The 2026-06-21 EBAW→EBNM flight, as logged from the airframe side."""
    fields = {
        "aircraft_id": acid,
        "date": date(2026, 6, 21),
        "departure_icao": "EBAW",
        "arrival_icao": "EBNM",
        "takeoff_time": time(8, 54),
        "landing_time": time(9, 30),
        "flight_time": Decimal("0.6"),
        "flight_time_counter_start": Decimal("1513.9"),
        "flight_time_counter_end": Decimal("1514.5"),
        "pic_name": "KLEIN",
        "notes": "VOL NA",
    }
    fields.update(overrides)
    fe = Flight(**fields)
    db.session.add(fe)
    db.session.commit()
    return fe


def _pilot_row(acid, uid, **overrides):
    """The same flight, as promoted from a personal-logbook import."""
    fields = {
        "aircraft_id": acid,
        "date": date(2026, 6, 21),
        "departure_icao": "EBAW",
        "arrival_icao": "EBNM",
        "departure_time": time(8, 48),
        "arrival_time": time(9, 36),
        "single_pilot_se": Decimal("0.8"),
        "function_pic": Decimal("0.8"),
        "engine_time": Decimal("0.8"),
        "pic_user_id": uid,
        "pic_name": "Klein",
        "notes": "Diana",
        "source": "logbook_import",
    }
    fields.update(overrides)
    fe = Flight(**fields)
    db.session.add(fe)
    db.session.commit()
    return fe


def _pilot_kwargs(**overrides):
    kwargs = {
        "date": date(2026, 6, 21),
        "other_aircraft_type": "DR401",
        "other_aircraft_type_icao": "DR40",
        "other_aircraft_registration": "OO-LKN",
        "departure_icao": "EBAW",
        "departure_time": time(8, 48),
        "arrival_icao": "EBNM",
        "arrival_time": time(9, 36),
        "pic_name": "Klein",
        "landings_day": 1,
        "landings_night": None,
        "single_pilot_se": 0.8,
        "function_pic": 0.8,
        "notes": "Diana",
    }
    kwargs.update(overrides)
    return kwargs


def _airframe_fields(**overrides):
    fields = {
        "date": date(2026, 6, 21),
        "departure_icao": "EBAW",
        "arrival_icao": "EBNM",
        "takeoff_time": time(8, 54),
        "landing_time": time(9, 30),
        "flight_time": 0.6,
        "flight_counter_start": 1513.9,
        "flight_counter_end": 1514.5,
        "notes": "VOL NA",
    }
    fields.update(overrides)
    return fields


# ── Pilot import onto an airframe-logged flight ──────────────────────────────


class TestMergeIntoExisting:
    def test_fill_only_completes_airframe_row(self, app):
        uid, acid = _world(app, "m1@example.com")
        with app.app_context():
            fe = _airframe_row(acid)
            merge_into_existing(fe, _pilot_kwargs(), uid, fill_only=True)
            db.session.commit()

            assert fe.pic_user_id == uid
            assert fe.departure_time == time(8, 48)
            assert fe.single_pilot_se == Decimal("0.8")
            # Airframe side untouched, engine side filled from block time.
            assert fe.flight_time == Decimal("0.6")
            assert fe.flight_time_counter_end == Decimal("1514.5")
            assert fe.engine_time == Decimal("0.8")
            # Existing values win; both remarks kept; no free-text aircraft.
            assert fe.pic_name == "KLEIN"
            assert fe.notes == "VOL NA\nDiana"
            assert fe.other_aircraft_registration is None

    def test_fill_only_does_not_repeat_remarks_already_present(self, app):
        uid, acid = _world(app, "m2@example.com")
        with app.app_context():
            fe = _airframe_row(acid, notes="VOL NA / Diana")
            merge_into_existing(fe, _pilot_kwargs(), uid, fill_only=True)
            assert fe.notes == "VOL NA / Diana"

    def test_overwrite_replaces_but_never_clears(self, app):
        uid, acid = _world(app, "m3@example.com")
        with app.app_context():
            fe = _pilot_row(acid, uid, single_pilot_se=Decimal("0.6"))
            merge_into_existing(
                fe, _pilot_kwargs(notes=None, pic_name="E. Klein"), uid, fill_only=False
            )
            db.session.commit()

            assert fe.single_pilot_se == Decimal("0.8")
            assert fe.pic_name == "E. Klein"
            assert fe.notes == "Diana"  # empty cell doesn't clear it
            assert fe.other_aircraft_registration is None
            assert fe.other_aircraft_type is None

    def test_overwrite_replaces_differing_remarks(self, app):
        uid, acid = _world(app, "m4@example.com")
        with app.app_context():
            fe = _pilot_row(acid, uid)
            merge_into_existing(
                fe, _pilot_kwargs(notes="Diana, Anabelle"), uid, fill_only=False
            )
            assert fe.notes == "Diana, Anabelle"

    def test_standalone_row_keeps_free_text_aircraft(self, app):
        uid, _acid = _world(app, "m5@example.com")
        with app.app_context():
            fe = Flight(date=date(2026, 6, 21), pic_user_id=uid)
            db.session.add(fe)
            db.session.flush()
            merge_into_existing(fe, _pilot_kwargs(), uid, fill_only=False)
            assert fe.other_aircraft_registration == "OO-LKN"
            assert fe.engine_time is None  # not a managed aircraft

    def test_fill_engine_time_needs_pilot_figures(self, app):
        _uid, acid = _world(app, "m6@example.com")
        with app.app_context():
            fe = _airframe_row(acid)
            _fill_engine_time(fe)
            assert fe.engine_time is None


class TestSplitAutoMerges:
    def _conflict(self, row_num, candidate_ids, **overrides):
        return ConflictRow(
            row_num=row_num,
            kwargs=_pilot_kwargs(**overrides),
            candidates=[(5.0, cid) for cid in candidate_ids],
        )

    def test_strong_match_with_airframe_only_row_merges(self, app):
        _uid, acid = _world(app, "s1@example.com")
        with app.app_context():
            fe = _airframe_row(acid)
            auto, review = split_auto_merges([self._conflict(1, [fe.id])])
            assert [(c.row_num, eid) for c, eid in auto] == [(1, fe.id)]
            assert review == []

    def test_each_existing_flight_claimed_once(self, app):
        _uid, acid = _world(app, "s2@example.com")
        with app.app_context():
            fe = _airframe_row(acid)
            auto, review = split_auto_merges(
                [self._conflict(1, [fe.id]), self._conflict(2, [fe.id])]
            )
            assert [c.row_num for c, _eid in auto] == [1]
            assert [c.row_num for c in review] == [2]

    def test_ambiguous_between_two_flights_goes_to_review(self, app):
        _uid, acid = _world(app, "s3@example.com")
        with app.app_context():
            a = _airframe_row(acid)
            b = _airframe_row(acid)
            auto, review = split_auto_merges([self._conflict(1, [a.id, b.id])])
            assert auto == []
            assert len(review) == 1

    def test_weak_or_claimed_matches_go_to_review(self, app):
        uid, acid = _world(app, "s4@example.com")
        with app.app_context():
            airframe = _airframe_row(acid)
            claimed = _pilot_row(acid, uid)
            cases = [
                self._conflict(1, [claimed.id]),  # pilot side already logged
                self._conflict(2, [airframe.id], other_aircraft_registration="OO-ICE"),
                self._conflict(3, [airframe.id], departure_icao=None),
                self._conflict(4, [airframe.id], arrival_icao="EBST"),
                self._conflict(5, [airframe.id], departure_time=time(7, 0)),
            ]
            auto, review = split_auto_merges(cases)
            assert auto == []
            assert [c.row_num for c in review] == [1, 2, 3, 4, 5]


# ── Airframe import onto a pilot-logged flight ───────────────────────────────


class TestMergeAirframeIntoExisting:
    def test_fill_only_completes_pilot_row(self, app):
        uid, acid = _world(app, "a1@example.com")
        with app.app_context():
            fe = _pilot_row(acid, uid)
            merge_airframe_into_existing(
                fe, _airframe_fields(), "KLEIN", fill_only=True
            )
            db.session.commit()

            assert fe.takeoff_time == time(8, 54)
            assert fe.flight_time == Decimal("0.6")
            assert fe.flight_time_counter_end == Decimal("1514.5")
            # Pilot/engine side untouched.
            assert fe.departure_time == time(8, 48)
            assert fe.engine_time == Decimal("0.8")
            assert fe.single_pilot_se == Decimal("0.8")
            assert fe.pic_name == "Klein"
            assert fe.notes == "Diana\nVOL NA"

    def test_fill_only_does_not_repeat_remarks_already_present(self, app):
        uid, acid = _world(app, "a2@example.com")
        with app.app_context():
            fe = _pilot_row(acid, uid, notes="Diana. VOL NA")
            merge_airframe_into_existing(fe, _airframe_fields(), None, fill_only=True)
            assert fe.notes == "Diana. VOL NA"

    def test_overwrite_never_clears_engine_side(self, app):
        uid, acid = _world(app, "a3@example.com")
        with app.app_context():
            fe = _pilot_row(
                acid,
                uid,
                engine_time_counter_start=Decimal("1920.1"),
                engine_time_counter_end=Decimal("1920.9"),
                pic_name=None,
            )
            merge_airframe_into_existing(
                fe, _airframe_fields(notes="Coolant low"), "KLEIN", fill_only=False
            )
            db.session.commit()

            assert fe.flight_time == Decimal("0.6")
            assert fe.engine_time_counter_start == Decimal("1920.1")
            assert fe.engine_time_counter_end == Decimal("1920.9")
            assert fe.departure_time == time(8, 48)
            assert fe.notes == "Coolant low"
            assert fe.pic_name == "KLEIN"


class TestSplitAirframeAutoMerges:
    def _conflict(self, row_num, candidate_ids, **overrides):
        return AirframeConflictRow(
            row_num=row_num,
            fields=_airframe_fields(**overrides),
            crew_name="KLEIN",
            candidates=[(4.0, cid) for cid in candidate_ids],
        )

    def test_strong_match_with_pilot_only_row_merges(self, app):
        uid, acid = _world(app, "b1@example.com")
        with app.app_context():
            fe = _pilot_row(acid, uid)
            ac = db.session.get(Aircraft, acid)
            auto, review = split_airframe_auto_merges(
                [self._conflict(1, [fe.id]), self._conflict(2, [fe.id])], ac
            )
            assert [(c.row_num, eid) for c, eid in auto] == [(1, fe.id)]
            assert [c.row_num for c in review] == [2]

    def test_weak_or_already_logged_matches_go_to_review(self, app):
        uid, acid = _world(app, "b2@example.com")
        with app.app_context():
            pilot = _pilot_row(acid, uid)
            no_times = _pilot_row(acid, uid, departure_time=None)
            airframe = _airframe_row(acid)
            ac = db.session.get(Aircraft, acid)
            cases = [
                self._conflict(1, [airframe.id]),  # airframe side already logged
                self._conflict(2, [pilot.id], departure_icao="ZZZZ"),
                self._conflict(3, [pilot.id], arrival_icao="EBST"),
                self._conflict(4, [pilot.id], landing_time=time(11, 0)),
                self._conflict(5, [no_times.id]),
            ]
            auto, review = split_airframe_auto_merges(cases, ac)
            assert auto == []
            assert [c.row_num for c in review] == [1, 2, 3, 4, 5]


# ── End-to-end through the import routes ─────────────────────────────────────


_PILOT_CSV = (
    "Date,Reg,From,Dep,To,Arr,SE,PIC,Ldg,Remarks\n"
    "21/06/26,OO-LKN,EBAW,08:48,EBNM,09:36,0.8,0.8,1,Diana\n"
    "22/06/26,OO-LKN,EBNM,11:06,EBAW,11:42,0.6,0.6,1,\n"
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


class TestPilotImportRoute:
    def test_airframe_logged_flight_merged_and_left_out_of_review(self, app, client):
        uid, acid = _world(app, "r1@example.com")
        with app.app_context():
            airframe_id = _airframe_row(acid).id
            # A flight already in this pilot's logbook with a different
            # duration — a genuine near-match needing a decision.
            _pilot_row(
                acid,
                uid,
                date=date(2026, 6, 22),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                departure_time=time(11, 6),
                arrival_time=time(11, 42),
                single_pilot_se=Decimal("0.5"),
            )
        with client.session_transaction() as sess:
            sess["user_id"] = uid
            sess["pilot_access"] = True

        client.post(
            "/pilot/logbook/import",
            data={"logbook_file": (BytesIO(_PILOT_CSV.encode()), "log.csv")},
            content_type="multipart/form-data",
        )
        rv = client.post("/pilot/logbook/import/execute", data=_PILOT_FORM)
        assert rv.headers["Location"].endswith("/pilot/logbook/import/review")
        with client.session_transaction() as sess:
            flashes = [msg for _cat, msg in sess.get("_flashes", [])]
        assert any(
            "One row matched a flight already in the aircraft log" in m for m in flashes
        )

        page = client.get("/pilot/logbook/import/review").data.decode()
        assert page.count('name="row_num"') == 1
        assert 'name="row_num" value="2"' in page

        rv = client.post(
            "/pilot/logbook/import/review/resolve",
            data={"row_num": "2", "decision": "keep"},
        )
        assert rv.headers["Location"].endswith("/pilot/logbook/import/history")
        with app.app_context():
            fe = db.session.get(Flight, airframe_id)
            assert fe.pic_user_id == uid
            assert fe.single_pilot_se == Decimal("0.8")
            assert fe.flight_time == Decimal("0.6")
            assert Flight.query.filter_by(aircraft_id=acid).count() == 2


_AIRFRAME_CSV = (
    "Date,Pilot,From,To,Takeoff,Landing,Duration,Fstart,Fend\n"
    "2026-06-21,KLEIN,EBAW,EBNM,08:54,09:30,0.6,1513.9,1514.5\n"
    "2026-06-22,KLEIN,EBNM,EBAW,11:12,11:36,0.4,1514.5,1514.9\n"
)
_AIRFRAME_FORM = {
    "mapping_date": "date",
    "mapping_pilot": "crew_name",
    "mapping_from": "departure_icao",
    "mapping_to": "arrival_icao",
    "mapping_takeoff": "takeoff_time",
    "mapping_landing": "landing_time",
    "mapping_duration": "flight_time",
    "mapping_fstart": "flight_counter_start",
    "mapping_fend": "flight_counter_end",
}


class TestAirframeImportRoute:
    def test_pilot_logged_flight_merged_and_left_out_of_review(self, app, client):
        uid, acid = _world(app, "r2@example.com")
        with app.app_context():
            pilot_id = _pilot_row(acid, uid).id
            # Already in the airframe log with a different duration.
            _airframe_row(
                acid,
                date=date(2026, 6, 22),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                takeoff_time=time(11, 12),
                landing_time=time(11, 36),
                flight_time=Decimal("0.5"),
                flight_time_counter_start=None,
                flight_time_counter_end=None,
            )
        with client.session_transaction() as sess:
            sess["user_id"] = uid

        client.post(
            f"/aircraft/{acid}/flights/import",
            data={
                "logbook_file": (BytesIO(_AIRFRAME_CSV.encode()), "af.csv", "text/csv")
            },
            content_type="multipart/form-data",
        )
        rv = client.post(
            f"/aircraft/{acid}/flights/import/execute", data=_AIRFRAME_FORM
        )
        assert rv.headers["Location"].endswith("/aircraft/OO-LKN/flights/import/review")
        with client.session_transaction() as sess:
            flashes = [msg for _cat, msg in sess.get("_flashes", [])]
        assert any(
            "One row matched a flight already in a pilot logbook" in m for m in flashes
        )

        page = client.get(f"/aircraft/{acid}/flights/import/review").data.decode()
        assert page.count('name="row_num"') == 1
        assert 'name="row_num" value="2"' in page

        rv = client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "2", "decision": "keep"},
        )
        assert "review" not in rv.headers["Location"]
        with app.app_context():
            fe = db.session.get(Flight, pilot_id)
            # Both sides of the same flight now on one row.
            assert fe.flight_time_counter_start == Decimal("1513.9")
            assert fe.flight_time_counter_end == Decimal("1514.5")
            assert fe.takeoff_time == time(8, 54)
            assert fe.engine_time == Decimal("0.8")
            assert fe.departure_time == time(8, 48)
            assert Flight.query.filter_by(aircraft_id=acid).count() == 2
