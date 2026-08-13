"""
Tests for bulk import of a historical airframe logbook (CSV/Excel):
  - upload → column mapping (alias + saved-fingerprint proposals) → execute
  - Flight row creation (pic_name free text), ICAO normalisation
  - counter-continuity warnings (never hard errors) and opening counters
  - batch rollback, role gating, and the failure paths
"""

import json
from datetime import date, time
from io import BytesIO

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from flights.airframe_import import (  # pyright: ignore[reportMissingImports]
    _clean_icao,
    _score_airframe_candidate,
    airframe_type_hints,
    find_conflicting_airframe_rows,
    propose_airframe_mapping,
)
from models import (
    Aircraft,
    AirframeImportBatch,
    AirframeImportMapping,
    Flight,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)  # pyright: ignore[reportMissingImports]
from pilots.logbook_import import parse_file  # pyright: ignore[reportMissingImports]

_CSV = (
    "Date,Pilot,From,To,Flight time,Landings,Hobbs start,Hobbs end,Remarks\n"
    "2020-05-01,Jean Dupont,EBOS,EBBR,1.5,2,100.0,101.6,First flight\n"
    "2020-05-08,Marie Curie,ebbr,eboskursaal,0.8,1,101.6,102.4,\n"
    "2020-05-15,,,,1.0,1,102.4,103.4,No pilot noted\n"
)

_CSV_GAP = (
    "Date,Pilot,Hobbs start,Hobbs end\n"
    "2020-05-01,Jean Dupont,100.0,101.0\n"
    "2020-05-08,Jean Dupont,105.0,106.0\n"  # 4-hour gap → continuity warning
)


def _create_user_and_tenant(app, email="owner@example.com", role=Role.OWNER):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email, password_hash=_pw_hash.hash("testpassword123"), is_active=True
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=role))
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="owner@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def _add_aircraft(app, tenant_id, registration="OO-IMP"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration=registration, make="Cessna", model="172S"
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _upload(client, acid, csv_text=_CSV, filename="airframe.csv"):
    return client.post(
        f"/aircraft/{acid}/flights/import",
        data={"logbook_file": (BytesIO(csv_text.encode()), filename, "text/csv")},
        content_type="multipart/form-data",
    )


_MAPPING_FORM = {
    "mapping_date": "date",
    "mapping_pilot": "crew_name",
    "mapping_from": "departure_icao",
    "mapping_to": "arrival_icao",
    "mapping_flight time": "flight_time",
    "mapping_landings": "landing_count",
    "mapping_hobbs start": "engine_counter_start",
    "mapping_hobbs end": "engine_counter_end",
    "mapping_remarks": "notes",
}


def _execute(client, acid, extra=None):
    data = dict(_MAPPING_FORM)
    if extra:
        data.update(extra)
    return client.post(
        f"/aircraft/{acid}/flights/import/execute", data=data, follow_redirects=False
    )


class TestHelpers:
    def test_clean_icao(self):
        assert _clean_icao(" ebos ") == "EBOS"
        assert _clean_icao("Oostende Airfield") == "OOST"
        assert _clean_icao(None) == "ZZZZ"
        assert _clean_icao("  ") == "ZZZZ"

    def test_alias_proposal(self):
        parsed = parse_file(_CSV.encode(), "airframe.csv")
        mapping, match_type = propose_airframe_mapping(parsed, [])
        assert match_type == "alias"
        assert mapping["date"] == "date"
        assert mapping["pilot"] == "crew_name"
        assert mapping["hobbs start"] == "engine_counter_start"

    def test_saved_fingerprint_proposal_filters_invalid_fields(self, app):
        parsed = parse_file(_CSV.encode(), "airframe.csv")
        stored = {col: "ignore" for col in parsed.norm_cols}
        stored["date"] = "date"
        stored["pilot"] = "not_a_real_field"
        saved = [
            type(
                "M",
                (),
                {
                    "source_fingerprint": parsed.fingerprint,
                    "column_mapping": json.dumps(stored),
                },
            )()
        ]
        mapping, match_type = propose_airframe_mapping(parsed, saved)
        assert match_type == "exact"
        assert mapping["date"] == "date"
        assert mapping["pilot"] == "ignore"

    def test_type_hints_flag_non_numeric_counter(self):
        csv_text = "Date,Pilot,From,Hobbs end\n2020-05-01,Jean,EBOS,not-a-number\n"
        parsed = parse_file(csv_text.encode(), "x.csv")
        hints = airframe_type_hints(
            parsed, {"date": "date", "hobbs end": "engine_counter_end"}
        )
        assert "hobbs end" in hints

    def test_type_hints_skip_empty_columns(self):
        csv_text = "Date,Pilot,From,Hobbs end\n2020-05-01,Jean,EBOS,\n"
        parsed = parse_file(csv_text.encode(), "x.csv")
        hints = airframe_type_hints(
            parsed, {"date": "date", "hobbs end": "engine_counter_end"}
        )
        assert hints == {}


class TestImportFlow:
    def test_upload_shows_mapping_page(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = _upload(client, acid)
        assert resp.status_code == 200
        assert b"Column mapping" in resp.data
        assert b"crew_name" in resp.data

    def test_execute_creates_entries_and_pic_name(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _upload(client, acid)
        resp = _execute(client, acid)
        assert resp.status_code == 302
        with app.app_context():
            entries = (
                Flight.query.filter_by(aircraft_id=acid).order_by(Flight.date).all()
            )
            assert len(entries) == 3
            first = entries[0]
            assert first.date == date(2020, 5, 1)
            assert first.departure_icao == "EBOS"
            assert float(first.flight_time) == 1.5
            assert first.landing_count == 2
            assert float(first.engine_time_counter_end) == 101.6
            assert first.source == "import"
            assert first.airframe_import_batch_id is not None
            assert first.pic_name == "Jean Dupont"
            assert first.pic_user_id is None
            # Second row: lowercase + overlong places normalised, pilot present
            second = entries[1]
            assert second.departure_icao == "EBBR"
            assert second.arrival_icao == "EBOS"
            assert second.pic_name == "Marie Curie"
            # Third row: no pilot → no pic_name, places default to ZZZZ
            third = entries[2]
            assert third.departure_icao == "ZZZZ"
            assert third.pic_name is None
            batch = AirframeImportBatch.query.filter_by(aircraft_id=acid).one()
            assert batch.row_count == 3
            assert batch.warning_count == 0

    def test_mapping_record_saved_and_reused(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _upload(client, acid)
        _execute(client, acid)
        with app.app_context():
            assert AirframeImportMapping.query.filter_by(tenant_id=tid).count() == 1
        # Second upload of the same format is recognised
        resp = _upload(client, acid)
        assert b"Recognised format" in resp.data
        # Executing again updates the same mapping record (no duplicate)
        _execute(client, acid)
        with app.app_context():
            assert AirframeImportMapping.query.filter_by(tenant_id=tid).count() == 1

    def test_continuity_warning_flashed(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _upload(client, acid, csv_text=_CSV_GAP)
        resp = client.post(
            f"/aircraft/{acid}/flights/import/execute",
            data={
                "mapping_date": "date",
                "mapping_hobbs start": "engine_counter_start",
                "mapping_hobbs end": "engine_counter_end",
            },
            follow_redirects=True,
        )
        assert b"Counter continuity warnings" in resp.data
        with app.app_context():
            batch = AirframeImportBatch.query.filter_by(aircraft_id=acid).one()
            assert batch.warning_count == 1

    def test_opening_counters_create_baseline(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _upload(client, acid, csv_text=_CSV_GAP)
        client.post(
            f"/aircraft/{acid}/flights/import/execute",
            data={
                "mapping_date": "date",
                "mapping_hobbs start": "engine_counter_start",
                "mapping_hobbs end": "engine_counter_end",
                "ob_engine_counter": "100.0",
                "ob_flight_counter": "90.0",
            },
        )
        with app.app_context():
            baseline = (
                Flight.query.filter_by(aircraft_id=acid).order_by(Flight.date).first()
            )
            assert baseline.date == date(2020, 4, 30)  # day before first flight
            assert float(baseline.engine_time_counter_end) == 100.0
            assert float(baseline.flight_time_counter_start) == 90.0
            assert baseline.notes == "Opening counters (imported)"
            batch = AirframeImportBatch.query.filter_by(aircraft_id=acid).one()
            assert batch.has_opening_counters is True

    def test_subtotal_rows_skipped_and_parse_warning_counted(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        csv_text = (
            "Date,Pilot,Landings,Remarks\n"
            "2020-05-01,Jean,two,ok\n"  # non-numeric landings → parse warning
            "TOTAL,,3,subtotal row\n"
        )
        _upload(client, acid, csv_text=csv_text)
        client.post(
            f"/aircraft/{acid}/flights/import/execute",
            data={
                "mapping_date": "date",
                "mapping_pilot": "crew_name",
                "mapping_landings": "landing_count",
                "mapping_remarks": "notes",
            },
        )
        with app.app_context():
            batch = AirframeImportBatch.query.filter_by(aircraft_id=acid).one()
            assert batch.row_count == 1
            assert batch.subtotal_count == 1
            entry = Flight.query.filter_by(aircraft_id=acid).one()
            assert entry.landing_count is None  # unparseable, warned, kept null

    def test_unparseable_dates_are_skipped(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        csv_text = (
            "Date,Pilot,From,Remarks\n"
            "2020-05-01,Jean,EBOS,ok\n"
            "not-a-date,Jean,EBOS,bad\n"
        )
        _upload(client, acid, csv_text=csv_text)
        resp = client.post(
            f"/aircraft/{acid}/flights/import/execute",
            data={"mapping_date": "date", "mapping_remarks": "notes"},
            follow_redirects=True,
        )
        assert b"Skipped rows" in resp.data
        with app.app_context():
            assert Flight.query.filter_by(aircraft_id=acid).count() == 1

    def test_many_warnings_and_skips_truncate_detail(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        rows = ["Date,Pilot,Hobbs start,Hobbs end"]
        counter = 100.0
        for i in range(1, 9):
            # every row starts 5 hours past the previous end → 7 warnings
            start = counter + 5.0
            rows.append(f"2020-05-{i:02d},Jean,{start:.1f},{start + 1.0:.1f}")
            counter = start + 1.0
        for i in range(1, 8):
            rows.append(f"garbage-{i},Jean,1.0,2.0")  # 7 unparseable dates
        _upload(client, acid, csv_text="\n".join(rows) + "\n")
        resp = client.post(
            f"/aircraft/{acid}/flights/import/execute",
            data={
                "mapping_date": "date",
                "mapping_hobbs start": "engine_counter_start",
                "mapping_hobbs end": "engine_counter_end",
            },
            follow_redirects=True,
        )
        assert b"more" in resp.data  # both details truncated with "… and N more"

    def test_rollback_removes_entries_and_batch(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _upload(client, acid)
        _execute(client, acid)
        with app.app_context():
            batch_id = AirframeImportBatch.query.filter_by(aircraft_id=acid).one().id
        resp = client.post(
            f"/aircraft/{acid}/flights/import/{batch_id}/rollback",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            assert Flight.query.filter_by(aircraft_id=acid).count() == 0
            assert db.session.get(AirframeImportBatch, batch_id) is None

    def test_rollback_of_foreign_batch_404(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        other = _add_aircraft(app, tid, registration="OO-OTH")
        _login(app, client)
        _upload(client, other)
        _execute(client, other)
        with app.app_context():
            batch_id = AirframeImportBatch.query.one().id
        resp = client.post(f"/aircraft/{acid}/flights/import/{batch_id}/rollback")
        assert resp.status_code == 404

    def test_upload_page_lists_batches(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _upload(client, acid)
        _execute(client, acid)
        resp = client.get(f"/aircraft/{acid}/flights/import")
        assert b"Previous imports" in resp.data
        assert b"airframe.csv" in resp.data
        assert b"Undo import" in resp.data


class TestGuardsAndFailures:
    def test_forbidden_for_pilot_role(self, app, client):
        _uid, tid = _create_user_and_tenant(
            app, email="pilot@example.com", role=Role.PILOT
        )
        acid = _add_aircraft(app, tid)
        _login(app, client, email="pilot@example.com")
        assert client.get(f"/aircraft/{acid}/flights/import").status_code == 403

    def test_missing_file_rejected(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{acid}/flights/import",
            data={},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 422

    def test_bad_extension_rejected(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = _upload(client, acid, filename="log.pdf")
        assert resp.status_code == 422
        assert b"Unsupported format" in resp.data

    def test_oversize_file_rejected(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        big = "Date\n" + ("2020-05-01\n" * 1_100_000)  # > 10 MB
        resp = _upload(client, acid, csv_text=big)
        assert resp.status_code == 422
        assert b"File too large" in resp.data

    def test_unparseable_file_rejected(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = _upload(client, acid, csv_text="")
        assert resp.status_code == 422

    def test_execute_without_session_redirects(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = _execute(client, acid)
        assert resp.status_code == 302
        assert "/aircraft/OO-IMP/flights/import" in resp.headers["Location"]

    def test_execute_with_missing_tmp_redirects(self, app, client):
        import os

        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _upload(client, acid)
        with client.session_transaction() as sess:
            os.remove(sess["airframe_import"]["tmp_path"])
        resp = _execute(client, acid)
        assert resp.status_code == 302

    def test_execute_with_corrupted_tmp_redirects(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _upload(client, acid)
        with client.session_transaction() as sess:
            tmp = sess["airframe_import"]["tmp_path"]
        with open(tmp, "wb") as fh:
            fh.write(b"")  # empty file no longer parses
        resp = _execute(client, acid)
        assert resp.status_code == 302

    def test_execute_without_date_mapping_rerenders(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _upload(client, acid)
        data = {k: "ignore" for k in _MAPPING_FORM}
        resp = client.post(
            f"/aircraft/{acid}/flights/import/execute",
            data=data,
            follow_redirects=False,
        )
        assert resp.status_code == 422
        assert b"Column mapping" in resp.data


class TestDuplicateDetection:
    def test_reimporting_same_file_skips_all_rows_as_duplicates(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="dup1@example.com")
        acid = _add_aircraft(app, tid, registration="OO-DUP1")
        _login(app, client, email="dup1@example.com")

        _upload(client, acid)
        rv1 = _execute(client, acid)
        assert rv1.status_code == 302

        with app.app_context():
            assert Flight.query.filter_by(aircraft_id=acid).count() == 3

        _upload(client, acid)
        rv2 = _execute(client, acid)
        assert rv2.status_code == 302

        with app.app_context():
            # No new rows created — all 3 re-parsed rows matched exactly.
            assert Flight.query.filter_by(aircraft_id=acid).count() == 3
            batches = AirframeImportBatch.query.filter_by(aircraft_id=acid).all()
            assert len(batches) == 2
            assert batches[1].row_count == 0

    def test_reimport_appends_only_new_rows(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="dup2@example.com")
        acid = _add_aircraft(app, tid, registration="OO-DUP2")
        _login(app, client, email="dup2@example.com")

        first_csv = (
            "Date,Pilot,From,To,Flight time,Landings,Hobbs start,Hobbs end,Remarks\n"
            "2020-05-01,Jean Dupont,EBOS,EBBR,1.5,2,100.0,101.6,First flight\n"
        )
        _upload(client, acid, csv_text=first_csv)
        assert _execute(client, acid).status_code == 302

        # Re-upload with the same row plus one genuinely new one.
        second_csv = first_csv + (
            "2020-05-08,Marie Curie,EBBR,EBOS,0.8,1,101.6,102.4,Second flight\n"
        )
        _upload(client, acid, csv_text=second_csv)
        rv = _execute(client, acid)
        assert rv.status_code == 302

        with app.app_context():
            assert Flight.query.filter_by(aircraft_id=acid).count() == 2
            batches = AirframeImportBatch.query.filter_by(aircraft_id=acid).all()
            assert batches[1].row_count == 1

        with client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert not any(cat == "warning" for cat, _msg in flashes)
        success_messages = [msg for cat, msg in flashes if cat == "success"]
        assert any("1 new flights imported" in msg for msg in success_messages)

    def test_reimport_all_duplicates_shows_neutral_message(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="dup3@example.com")
        acid = _add_aircraft(app, tid, registration="OO-DUP3")
        _login(app, client, email="dup3@example.com")

        csv_text = (
            "Date,Pilot,From,To,Flight time,Landings,Hobbs start,Hobbs end,Remarks\n"
            "2020-05-01,Jean Dupont,EBOS,EBBR,1.5,2,100.0,101.6,First flight\n"
        )
        _upload(client, acid, csv_text=csv_text)
        assert _execute(client, acid).status_code == 302

        _upload(client, acid, csv_text=csv_text)
        rv = _execute(client, acid)
        assert rv.status_code == 302

        with client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert not any(cat == "warning" for cat, _msg in flashes)
        success_messages = [msg for cat, msg in flashes if cat == "success"]
        assert any("nothing new was imported" in msg for msg in success_messages)

    def test_reimport_more_than_5_duplicates_truncates_detail(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="dup4@example.com")
        acid = _add_aircraft(app, tid, registration="OO-DUP4")
        _login(app, client, email="dup4@example.com")

        rows = "\n".join(
            f"2020-0{i + 1}-01,Jean,EBOS,EBBR,1.0,1,{100 + i}.0,{101 + i}.0,"
            for i in range(6)
        )
        csv_text = (
            "Date,Pilot,From,To,Flight time,Landings,Hobbs start,Hobbs end,Remarks\n"
            f"{rows}\n"
        )
        _upload(client, acid, csv_text=csv_text)
        assert _execute(client, acid).status_code == 302

        _upload(client, acid, csv_text=csv_text)
        rv = _execute(client, acid)
        assert rv.status_code == 302

        with client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        info_messages = [msg for cat, msg in flashes if cat == "info"]
        assert any("and 1 more" in msg for msg in info_messages)

    def test_duplicate_detection_scoped_per_aircraft(self, app, client):
        """The same flight data on two different aircraft is not a duplicate
        — the dedup key is scoped to aircraft_id."""
        _uid, tid = _create_user_and_tenant(app, email="dup5@example.com")
        ac1 = _add_aircraft(app, tid, registration="OO-DUP5A")
        ac2 = _add_aircraft(app, tid, registration="OO-DUP5B")
        _login(app, client, email="dup5@example.com")

        csv_text = (
            "Date,Pilot,From,To,Flight time,Landings,Hobbs start,Hobbs end,Remarks\n"
            "2020-05-01,Jean Dupont,EBOS,EBBR,1.5,2,100.0,101.6,First flight\n"
        )
        _upload(client, ac1, csv_text=csv_text)
        assert _execute(client, ac1).status_code == 302

        _upload(client, ac2, csv_text=csv_text)
        assert _execute(client, ac2).status_code == 302

        with app.app_context():
            assert Flight.query.filter_by(aircraft_id=ac1).count() == 1
            assert Flight.query.filter_by(aircraft_id=ac2).count() == 1


class TestParseRowDate:
    def test_returns_none_when_no_date_mapped(self):
        from flights.airframe_import import (
            _parse_row_date,  # pyright: ignore[reportMissingImports]
        )

        assert _parse_row_date(["15/03/24"], {"col": "ignore"}, {"col": 0}) is None


class TestAirframeConflictScoring:
    def _entry(self, **kw):
        defaults: dict = {
            "aircraft_id": 1,
            "date": date(2024, 3, 15),
            "departure_icao": "ZZZZ",
            "arrival_icao": "ZZZZ",
            "takeoff_time": None,
            "landing_time": None,
            "flight_time": None,
            "landing_count": None,
            "flight_time_counter_end": None,
        }
        defaults.update(kw)
        return Flight(**defaults)

    def test_score_full_match(self, app):
        with app.app_context():
            existing = self._entry(
                departure_icao="EBOS",
                arrival_icao="EBBR",
                takeoff_time=time(9, 0),
                landing_time=time(10, 0),
                flight_time=1.5,
                landing_count=2,
                flight_time_counter_end=101.6,
            )
            fields = {
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "takeoff_time": time(9, 0),
                "landing_time": time(10, 0),
                "flight_time": 1.5,
                "landing_count": 2,
                "flight_counter_end": 101.7,
            }
            assert _score_airframe_candidate(fields, existing, 0.3) == 7

    def test_score_same_pair_graduated_tiers(self, app):
        """existing.takeoff_time set (a previous airframe import) — scored
        tight (SAME_PAIR_STEP_MINUTES=5) against the new row's takeoff_time,
        not the offset-derived band used for the cross-pair fallback below."""
        with app.app_context():
            existing = self._entry(takeoff_time=time(9, 0))
            assert (
                _score_airframe_candidate({"takeoff_time": time(9, 0)}, existing, 0.3)
                == 1.0
            )
            assert (
                _score_airframe_candidate({"takeoff_time": time(9, 5)}, existing, 0.3)
                == 0.75
            )
            assert (
                _score_airframe_candidate({"takeoff_time": time(9, 10)}, existing, 0.3)
                == 0.5
            )
            assert (
                _score_airframe_candidate({"takeoff_time": time(9, 11)}, existing, 0.3)
                == 0.0
            )

    def test_score_cross_pair_fallback_centred_on_offset(self, app):
        """existing.takeoff_time absent, existing.departure_time set (a
        pilot-import placeholder) — scored against departure_time shifted
        by the aircraft's flight_counter_offset (0.3h → centre at +18min,
        ring width 6min), not against departure_time itself."""
        with app.app_context():
            existing = self._entry(departure_time=time(9, 0), takeoff_time=None)
            assert (
                _score_airframe_candidate({"takeoff_time": time(9, 18)}, existing, 0.3)
                == 1.0
            )
            assert (
                _score_airframe_candidate({"takeoff_time": time(9, 24)}, existing, 0.3)
                == 0.75
            )
            assert (
                _score_airframe_candidate({"takeoff_time": time(9, 30)}, existing, 0.3)
                == 0.5
            )
            assert (
                _score_airframe_candidate({"takeoff_time": time(9, 31)}, existing, 0.3)
                == 0.0
            )
            # Same as departure_time itself (0 diff from the centre would
            # be 9:18, so departure_time=9:00 is 18 min away — outside even
            # the loose ring at the default 0.3h offset).
            assert (
                _score_airframe_candidate({"takeoff_time": time(9, 0)}, existing, 0.3)
                == 0.0
            )

    def test_score_landing_cross_pair_fallback_shifts_earlier(self, app):
        """landing_time (wheels-down) is expected *before* arrival_time
        (block-on) — the opposite direction from takeoff_time/departure_time."""
        with app.app_context():
            existing = self._entry(arrival_time=time(10, 30), landing_time=None)
            assert (
                _score_airframe_candidate({"landing_time": time(10, 12)}, existing, 0.3)
                == 1.0
            )

    def test_score_no_existing_time_at_all_scores_zero_for_that_dimension(self, app):
        with app.app_context():
            existing = self._entry()
            assert (
                _score_airframe_candidate({"takeoff_time": time(9, 0)}, existing, 0.3)
                == 0.0
            )

    def test_score_zzzz_route_is_neutral_not_a_match(self, app):
        """Unmapped ICAO ('ZZZZ' on both sides) must not count as a match —
        it carries no real route information."""
        with app.app_context():
            existing = self._entry(flight_time=1.5, landing_count=2)
            fields = {
                "departure_icao": "ZZZZ",
                "arrival_icao": "ZZZZ",
                "flight_time": 1.5,
                "landing_count": 2,
            }
            # Only duration + landings — route is neutral, not counted.
            assert _score_airframe_candidate(fields, existing, 0.3) == 2

    def test_score_mismatched_route_not_counted(self, app):
        with app.app_context():
            existing = self._entry(departure_icao="EBOS")
            fields = {"departure_icao": "EBBR"}
            assert _score_airframe_candidate(fields, existing, 0.3) == 0

    def test_score_duration_outside_tolerance_not_counted(self, app):
        with app.app_context():
            existing = self._entry(
                departure_icao="EBOS", arrival_icao="EBBR", flight_time=1.0
            )
            fields = {
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time": 5.0,
            }
            assert _score_airframe_candidate(fields, existing, 0.3) == 2


class TestFindConflictingAirframeRows:
    def _mapping(self):
        return {
            "date": "date",
            "from": "departure_icao",
            "to": "arrival_icao",
            "flight time": "flight_time",
            "landings": "landing_count",
        }

    def _make_parsed(self, rows):
        from pilots.logbook_import import (  # pyright: ignore[reportMissingImports]
            ParsedFile,
            _fingerprint,
        )

        cols = ["date", "from", "to", "flight time", "landings"]
        return ParsedFile(
            norm_cols=cols,
            raw_cols=cols,
            header_row_index=0,
            data_rows=rows,
            fingerprint=_fingerprint(cols),
        )

    def test_finds_near_match_row(self, app):
        _uid, tid = _create_user_and_tenant(app, email="fca1@example.com")
        acid = _add_aircraft(app, tid, registration="OO-FCA1")
        with app.app_context():
            existing = Flight(
                aircraft_id=acid,
                date=date(2024, 3, 15),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                flight_time=1.0,
                landing_count=1,
            )
            db.session.add(existing)
            db.session.commit()

            parsed = self._make_parsed([["15/03/24", "EBOS", "EBBR", "1.1", "1"]])
            conflicts = find_conflicting_airframe_rows(parsed, self._mapping(), acid)
            assert len(conflicts) == 1
            assert conflicts[0].row_num == 1
            assert conflicts[0].candidates[0][1] == existing.id

    def test_excludes_exact_duplicates(self, app):
        _uid, tid = _create_user_and_tenant(app, email="fca2@example.com")
        acid = _add_aircraft(app, tid, registration="OO-FCA2")
        with app.app_context():
            existing = Flight(
                aircraft_id=acid,
                date=date(2024, 3, 15),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                flight_time=1.0,
                landing_count=1,
            )
            db.session.add(existing)
            db.session.commit()

            parsed = self._make_parsed(
                [["15/03/24", "EBOS", "EBBR", "1.0", "1"]]  # identical
            )
            assert find_conflicting_airframe_rows(parsed, self._mapping(), acid) == []

    def test_excludes_low_score_rows(self, app):
        _uid, tid = _create_user_and_tenant(app, email="fca3@example.com")
        acid = _add_aircraft(app, tid, registration="OO-FCA3")
        with app.app_context():
            existing = Flight(
                aircraft_id=acid,
                date=date(2024, 3, 15),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                flight_time=1.0,
            )
            db.session.add(existing)
            db.session.commit()

            parsed = self._make_parsed([["15/03/24", "LFPG", "LFPO", "5.0", ""]])
            assert find_conflicting_airframe_rows(parsed, self._mapping(), acid) == []

    def test_ambiguous_multiple_candidates(self, app):
        _uid, tid = _create_user_and_tenant(app, email="fca4@example.com")
        acid = _add_aircraft(app, tid, registration="OO-FCA4")
        with app.app_context():
            e1 = Flight(
                aircraft_id=acid,
                date=date(2024, 3, 15),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                flight_time=1.0,
                landing_count=1,
            )
            e2 = Flight(
                aircraft_id=acid,
                date=date(2024, 3, 15),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                flight_time=1.9,
                landing_count=1,
            )
            db.session.add_all([e1, e2])
            db.session.commit()

            parsed = self._make_parsed([["15/03/24", "EBOS", "EBBR", "1.4", "1"]])
            conflicts = find_conflicting_airframe_rows(parsed, self._mapping(), acid)
            assert len(conflicts) == 1
            ids = {cid for _score, cid in conflicts[0].candidates}
            assert ids == {e1.id, e2.id}

    def test_exclude_row_nums_param(self, app):
        _uid, tid = _create_user_and_tenant(app, email="fca5@example.com")
        acid = _add_aircraft(app, tid, registration="OO-FCA5")
        with app.app_context():
            existing = Flight(
                aircraft_id=acid,
                date=date(2024, 3, 15),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                flight_time=1.0,
                landing_count=1,
            )
            db.session.add(existing)
            db.session.commit()

            parsed = self._make_parsed([["15/03/24", "EBOS", "EBBR", "1.1", "1"]])
            conflicts = find_conflicting_airframe_rows(
                parsed, self._mapping(), acid, exclude_row_nums={1}
            )
            assert conflicts == []

    def test_skips_subtotal_and_unparseable_date_rows(self, app):
        from datetime import timedelta

        _uid, tid = _create_user_and_tenant(app, email="fca6@example.com")
        acid = _add_aircraft(app, tid, registration="OO-FCA6")
        with app.app_context():
            parsed = self._make_parsed(
                [
                    ["not-a-date", "EBOS", "EBBR", "1.0", "1"],
                    [timedelta(hours=10), "subtotal", "", "10", ""],
                ]
            )
            assert find_conflicting_airframe_rows(parsed, self._mapping(), acid) == []


class TestAirframeImportReviewRoute:
    """Integration tests for the conflict-review step of the airframe import wizard."""

    def _seed_near_match(self, app, acid: int) -> int:
        """A single existing flight that a re-upload with a slightly
        different duration will score >= 3 against (route + landings +
        close duration)."""
        with app.app_context():
            existing = Flight(
                aircraft_id=acid,
                date=date(2020, 5, 1),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                flight_time=1.0,
                landing_count=2,
            )
            db.session.add(existing)
            db.session.commit()
            return existing.id

    def _conflict_csv(self):
        return "Date,From,To,Flight time,Landings\n2020-05-01,EBOS,EBBR,1.4,2\n"

    def _execute_conflict(self, client, acid):
        return client.post(
            f"/aircraft/{acid}/flights/import/execute",
            data={
                "mapping_date": "date",
                "mapping_from": "departure_icao",
                "mapping_to": "arrival_icao",
                "mapping_flight time": "flight_time",
                "mapping_landings": "landing_count",
            },
            follow_redirects=False,
        )

    def test_conflict_row_routes_to_review(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="arv1@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV1")
        self._seed_near_match(app, acid)
        _login(app, client, email="arv1@example.com")

        _upload(client, acid, csv_text=self._conflict_csv())
        rv = self._execute_conflict(client, acid)
        assert rv.status_code == 302
        assert "/import/review" in rv.headers["Location"]

    def test_review_get_no_session_redirects(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="arv2@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV2")
        _login(app, client, email="arv2@example.com")

        rv = client.get(f"/aircraft/{acid}/flights/import/review")
        assert rv.status_code == 302
        assert "/flights/import" in rv.headers["Location"]

    def test_resolve_no_session_redirects(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="arv3@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV3")
        _login(app, client, email="arv3@example.com")

        rv = client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "1", "decision": "keep"},
        )
        assert rv.status_code == 302
        assert "/flights/import" in rv.headers["Location"]

    def test_review_get_renders_comparison_page(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="arv4@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV4")
        self._seed_near_match(app, acid)
        _login(app, client, email="arv4@example.com")

        _upload(client, acid, csv_text=self._conflict_csv())
        self._execute_conflict(client, acid)

        rv = client.get(f"/aircraft/{acid}/flights/import/review")
        assert rv.status_code == 200
        assert b"Review possible corrections" in rv.data
        assert b"row_num" in rv.data
        assert b"1.4" in rv.data

    def test_resolve_keep_leaves_existing_unchanged(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="arv5@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV5")
        existing_id = self._seed_near_match(app, acid)
        _login(app, client, email="arv5@example.com")

        _upload(client, acid, csv_text=self._conflict_csv())
        self._execute_conflict(client, acid)

        rv = client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "1", "decision": "keep"},
        )
        assert rv.status_code == 302
        assert "flights" in rv.headers["Location"]

        with app.app_context():
            entry = db.session.get(Flight, existing_id)
            assert float(entry.flight_time) == 1.0
            assert Flight.query.filter_by(aircraft_id=acid).count() == 1

    def test_resolve_overwrite_updates_existing(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="arv6@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV6")
        existing_id = self._seed_near_match(app, acid)
        _login(app, client, email="arv6@example.com")

        _upload(client, acid, csv_text=self._conflict_csv())
        self._execute_conflict(client, acid)

        rv = client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "1", "decision": f"overwrite:{existing_id}"},
        )
        assert rv.status_code == 302

        with app.app_context():
            entry = db.session.get(Flight, existing_id)
            assert float(entry.flight_time) == 1.4
            # Untouched by the overwrite — stays outside the new batch's rollback.
            assert entry.airframe_import_batch_id is None
            assert Flight.query.filter_by(aircraft_id=acid).count() == 1

    def test_resolve_overwrite_preserves_existing_pic_name(self, app, client):
        """Overwriting a matched flight that already has a pic_name set must
        not clobber it with the freshly-imported row's crew name — only a
        blank pic_name is filled in by an overwrite (see
        airframe_import_review_resolve's `not existing.pic_name` guard)."""
        _uid, tid = _create_user_and_tenant(app, email="arv7@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV7")
        existing_id = self._seed_near_match(app, acid)
        with app.app_context():
            existing = db.session.get(Flight, existing_id)
            existing.pic_name = "Jean Dupont"
            db.session.commit()
        _login(app, client, email="arv7@example.com")

        csv_text = "Date,Pilot,From,To,Flight time,Landings\n2020-05-01,Someone Else,EBOS,EBBR,1.4,2\n"
        _upload(client, acid, csv_text=csv_text)
        client.post(
            f"/aircraft/{acid}/flights/import/execute",
            data={
                "mapping_date": "date",
                "mapping_pilot": "crew_name",
                "mapping_from": "departure_icao",
                "mapping_to": "arrival_icao",
                "mapping_flight time": "flight_time",
                "mapping_landings": "landing_count",
            },
            follow_redirects=False,
        )
        client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "1", "decision": f"overwrite:{existing_id}"},
        )

        with app.app_context():
            entry = db.session.get(Flight, existing_id)
            assert entry.pic_name == "Jean Dupont"

    def test_resolve_overwrite_merges_into_pilot_import_placeholder(self, app, client):
        """Step 2 (docs/backlog.md "reconcile imports from either side"):
        an airframe import landing on a row a pilot logbook import already
        created (source="logbook_import", real counters still NULL) must
        fill in the missing airframe-side fields without disturbing the
        pilot-side identity/EASA data already on the row — the existing
        overwrite resolution already only ever touches airframe fields
        (_fields_to_flight_entry_kwargs has no EASA/identity keys), so this
        locks that behaviour in for the placeholder-row case specifically.

        Also covers the departure_time/arrival_time (pilot-log-facing, set
        by the earlier pilot import) vs takeoff_time/landing_time
        (airframe-log-facing, set by this import) split: the airframe file
        uses deliberately different clock times than the placeholder so a
        regression that clobbers the wrong pair would show up as a value
        mismatch, not just a missing assertion."""
        from decimal import Decimal

        uid, tid = _create_user_and_tenant(app, email="arv7b@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV7B")
        with app.app_context():
            placeholder = Flight(
                aircraft_id=acid,
                source="logbook_import",
                date=date(2020, 5, 1),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                departure_time=time(9, 0),
                arrival_time=time(10, 30),
                pic_user_id=uid,
                pic_name="Jean Dupont",
                single_pilot_se=Decimal("1.5"),
                function_pic=Decimal("1.5"),
                landings_day=1,
            )
            db.session.add(placeholder)
            db.session.commit()
            placeholder_id = placeholder.id
        _login(app, client, email="arv7b@example.com")

        csv_text = (
            "Date,From,To,Departure,Arrival,Flight time,"
            "Flight counter start,Flight counter end\n"
            "2020-05-01,EBOS,EBBR,09:06,10:24,1.5,500.0,501.5\n"
        )
        _upload(client, acid, csv_text=csv_text)
        client.post(
            f"/aircraft/{acid}/flights/import/execute",
            data={
                "mapping_date": "date",
                "mapping_from": "departure_icao",
                "mapping_to": "arrival_icao",
                "mapping_departure": "takeoff_time",
                "mapping_arrival": "landing_time",
                "mapping_flight time": "flight_time",
                "mapping_flight counter start": "flight_counter_start",
                "mapping_flight counter end": "flight_counter_end",
            },
            follow_redirects=False,
        )
        rv = client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "1", "decision": f"overwrite:{placeholder_id}"},
        )
        assert rv.status_code == 302

        with app.app_context():
            entry = db.session.get(Flight, placeholder_id)
            # Filled in from the airframe side.
            assert float(entry.flight_time_counter_start) == 500.0
            assert float(entry.flight_time_counter_end) == 501.5
            assert entry.takeoff_time == time(9, 6)
            assert entry.landing_time == time(10, 24)
            # Untouched — the pilot import's identity/EASA data and its own
            # departure_time/arrival_time (a different pair, different
            # times) survive.
            assert entry.pic_user_id == uid
            assert entry.pic_name == "Jean Dupont"
            assert float(entry.single_pilot_se) == 1.5
            assert float(entry.function_pic) == 1.5
            assert entry.landings_day == 1
            assert entry.departure_time == time(9, 0)
            assert entry.arrival_time == time(10, 30)
            assert Flight.query.filter_by(aircraft_id=acid).count() == 1

    def test_resolve_new_creates_separate_entry(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="arv8@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV8")
        existing_id = self._seed_near_match(app, acid)
        _login(app, client, email="arv8@example.com")

        _upload(client, acid, csv_text=self._conflict_csv())
        self._execute_conflict(client, acid)

        rv = client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "1", "decision": "new"},
        )
        assert rv.status_code == 302

        with app.app_context():
            entry = db.session.get(Flight, existing_id)
            assert float(entry.flight_time) == 1.0  # untouched
            assert Flight.query.filter_by(aircraft_id=acid).count() == 2
            batch = AirframeImportBatch.query.filter_by(aircraft_id=acid).first()
            assert batch.row_count == 1

    def test_resolve_invalid_row_num_format(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="arv9@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV9")
        self._seed_near_match(app, acid)
        _login(app, client, email="arv9@example.com")

        _upload(client, acid, csv_text=self._conflict_csv())
        self._execute_conflict(client, acid)

        rv = client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "not-a-number", "decision": "keep"},
        )
        assert rv.status_code == 302
        assert "/import/review" in rv.headers["Location"]

    def test_resolve_unknown_row_num(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="arv10@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV10")
        self._seed_near_match(app, acid)
        _login(app, client, email="arv10@example.com")

        _upload(client, acid, csv_text=self._conflict_csv())
        self._execute_conflict(client, acid)

        rv = client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "999", "decision": "keep"},
        )
        assert rv.status_code == 302
        assert "/import/review" in rv.headers["Location"]

    def test_resolve_already_resolved_row_num_mid_review(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="arv11@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV11")
        self._seed_near_match(app, acid)
        with app.app_context():
            db.session.add(
                Flight(
                    aircraft_id=acid,
                    date=date(2020, 6, 1),
                    departure_icao="LFPG",
                    arrival_icao="LFPO",
                    flight_time=2.0,
                    landing_count=1,
                )
            )
            db.session.commit()
        _login(app, client, email="arv11@example.com")

        csv_text = (
            "Date,From,To,Flight time,Landings\n"
            "2020-05-01,EBOS,EBBR,1.4,2\n"
            "2020-06-01,LFPG,LFPO,2.4,1\n"
        )
        _upload(client, acid, csv_text=csv_text)
        client.post(
            f"/aircraft/{acid}/flights/import/execute",
            data={
                "mapping_date": "date",
                "mapping_from": "departure_icao",
                "mapping_to": "arrival_icao",
                "mapping_flight time": "flight_time",
                "mapping_landings": "landing_count",
            },
            follow_redirects=False,
        )

        rv1 = client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "1", "decision": "keep"},
        )
        assert rv1.status_code == 302
        assert "/import/review" in rv1.headers["Location"]

        rv2 = client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "1", "decision": "keep"},
        )
        assert rv2.status_code == 302
        assert "/import/review" in rv2.headers["Location"]

    def test_resolve_overwrite_invalid_candidate_id(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="arv12@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV12")
        self._seed_near_match(app, acid)
        _login(app, client, email="arv12@example.com")

        _upload(client, acid, csv_text=self._conflict_csv())
        self._execute_conflict(client, acid)

        rv = client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "1", "decision": "overwrite:999999"},
        )
        assert rv.status_code == 302
        assert "/import/review" in rv.headers["Location"]

    def test_resolve_overwrite_non_numeric_candidate_id(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="arv13@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV13")
        self._seed_near_match(app, acid)
        _login(app, client, email="arv13@example.com")

        _upload(client, acid, csv_text=self._conflict_csv())
        self._execute_conflict(client, acid)

        rv = client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "1", "decision": "overwrite:notanumber"},
        )
        assert rv.status_code == 302
        assert "/import/review" in rv.headers["Location"]

    def test_resolve_invalid_decision(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="arv14@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV14")
        self._seed_near_match(app, acid)
        _login(app, client, email="arv14@example.com")

        _upload(client, acid, csv_text=self._conflict_csv())
        self._execute_conflict(client, acid)

        rv = client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "1", "decision": "bogus"},
        )
        assert rv.status_code == 302
        assert "/import/review" in rv.headers["Location"]

    def test_review_tmp_file_missing_redirects(self, app, client):
        import os

        _uid, tid = _create_user_and_tenant(app, email="arv15@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV15")
        self._seed_near_match(app, acid)
        _login(app, client, email="arv15@example.com")

        _upload(client, acid, csv_text=self._conflict_csv())
        self._execute_conflict(client, acid)

        with client.session_transaction() as sess:
            tmp_path = sess["airframe_import_review"]["tmp_path"]
        os.remove(tmp_path)

        rv = client.get(f"/aircraft/{acid}/flights/import/review")
        assert rv.status_code == 302
        assert "/flights/import" in rv.headers["Location"]

    def test_resolve_tmp_file_missing_redirects(self, app, client):
        import os

        _uid, tid = _create_user_and_tenant(app, email="arv16@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV16")
        self._seed_near_match(app, acid)
        _login(app, client, email="arv16@example.com")

        _upload(client, acid, csv_text=self._conflict_csv())
        self._execute_conflict(client, acid)

        with client.session_transaction() as sess:
            tmp_path = sess["airframe_import_review"]["tmp_path"]
        os.remove(tmp_path)

        rv = client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "1", "decision": "keep"},
        )
        assert rv.status_code == 302
        assert "/flights/import" in rv.headers["Location"]

    def test_review_get_finalizes_when_no_conflicts_remain(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="arv17@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV17")
        existing_id = self._seed_near_match(app, acid)
        _login(app, client, email="arv17@example.com")

        _upload(client, acid, csv_text=self._conflict_csv())
        self._execute_conflict(client, acid)

        with app.app_context():
            db.session.delete(db.session.get(Flight, existing_id))
            db.session.commit()

        rv = client.get(f"/aircraft/{acid}/flights/import/review")
        assert rv.status_code == 302
        assert "flights" in rv.headers["Location"]

    def test_new_upload_cleans_up_pending_review_tmp_file(self, app, client):
        import os

        _uid, tid = _create_user_and_tenant(app, email="arv18@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV18")
        self._seed_near_match(app, acid)
        _login(app, client, email="arv18@example.com")

        _upload(client, acid, csv_text=self._conflict_csv())
        self._execute_conflict(client, acid)

        with client.session_transaction() as sess:
            old_tmp_path = sess["airframe_import_review"]["tmp_path"]
        assert os.path.isfile(old_tmp_path)

        rv = _upload(client, acid, csv_text=_CSV, filename="other.csv")
        assert rv.status_code == 200
        assert not os.path.isfile(old_tmp_path)
        with client.session_transaction() as sess:
            assert "airframe_import_review" not in sess

    def test_finalize_activity_and_summary_flash(self, app, client):
        _uid, tid = _create_user_and_tenant(app, email="arv19@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV19")
        self._seed_near_match(app, acid)
        _login(app, client, email="arv19@example.com")

        _upload(client, acid, csv_text=self._conflict_csv())
        self._execute_conflict(client, acid)

        rv = client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "1", "decision": "new"},
            follow_redirects=True,
        )
        assert rv.status_code == 200
        assert b"imported as new" in rv.data

    def test_execute_conflict_path_truncates_long_duplicate_and_skipped_lists(
        self, app, client
    ):
        """Cover the '… and N more' truncation branches inside the
        conflict-detected path of airframe_import_execute."""
        _uid, tid = _create_user_and_tenant(app, email="arv20@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV20")
        self._seed_near_match(app, acid)
        with app.app_context():
            for i in range(6):
                db.session.add(
                    Flight(
                        aircraft_id=acid,
                        date=date(2020, 6, 1 + i),
                        departure_icao="LFPG",
                        arrival_icao="LFPO",
                        flight_time=0.5,
                    )
                )
            db.session.commit()
        _login(app, client, email="arv20@example.com")

        dup_rows = "\n".join(f"2020-06-0{i + 1},LFPG,LFPO,0.5," for i in range(6))
        bad_rows = "\n".join(f"baddate{i},EBOS,EBBR,0.5," for i in range(6))
        csv_text = (
            "Date,From,To,Flight time,Landings\n"
            "2020-05-01,EBOS,EBBR,1.4,2\n"
            f"{dup_rows}\n"
            f"{bad_rows}\n"
        )
        _upload(client, acid, csv_text=csv_text)
        rv = self._execute_conflict(client, acid)
        assert rv.status_code == 302
        assert "/import/review" in rv.headers["Location"]

        with client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        info_messages = [msg for cat, msg in flashes if cat == "info"]
        warning_messages = [msg for cat, msg in flashes if cat == "warning"]
        assert any("and 1 more" in msg for msg in info_messages)
        assert any("and 1 more" in msg for msg in warning_messages)

    def test_review_get_reparse_fails_redirects(self, app, client):
        import os
        import tempfile

        _uid, tid = _create_user_and_tenant(app, email="arv21@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV21")
        _login(app, client, email="arv21@example.com")

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(b"1.0,2.0,3.0\n4.0,5.0,6.0\n")
            tmp = f.name

        with client.session_transaction() as sess:
            sess["airframe_import_review"] = {
                "aircraft_id": acid,
                "tmp_path": tmp,
                "original_filename": "bad.csv",
                "mapping": {"date": "date"},
                "batch_id": 1,
                "resolved": {},
            }

        rv = client.get(f"/aircraft/{acid}/flights/import/review")
        assert rv.status_code == 302
        assert "/flights/import" in rv.headers["Location"]

        if os.path.isfile(tmp):
            os.remove(tmp)

    def test_resolve_reparse_fails_redirects(self, app, client):
        import os
        import tempfile

        _uid, tid = _create_user_and_tenant(app, email="arv22@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV22")
        _login(app, client, email="arv22@example.com")

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(b"1.0,2.0,3.0\n4.0,5.0,6.0\n")
            tmp = f.name

        with client.session_transaction() as sess:
            sess["airframe_import_review"] = {
                "aircraft_id": acid,
                "tmp_path": tmp,
                "original_filename": "bad.csv",
                "mapping": {"date": "date"},
                "batch_id": 1,
                "resolved": {},
            }

        rv = client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "1", "decision": "keep"},
        )
        assert rv.status_code == 302
        assert "/flights/import" in rv.headers["Location"]

        if os.path.isfile(tmp):
            os.remove(tmp)

    def test_resolve_overwrite_sets_pic_name_when_none_exists_yet(self, app, client):
        """Cover the 'existing flight has no pic_name yet' branch of the
        overwrite decision (as opposed to the already-has-it case covered by
        test_resolve_overwrite_preserves_existing_pic_name)."""
        _uid, tid = _create_user_and_tenant(app, email="arv23@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV23")
        existing_id = self._seed_near_match(app, acid)
        _login(app, client, email="arv23@example.com")

        csv_text = "Date,Pilot,From,To,Flight time,Landings\n2020-05-01,New Pilot,EBOS,EBBR,1.4,2\n"
        _upload(client, acid, csv_text=csv_text)
        client.post(
            f"/aircraft/{acid}/flights/import/execute",
            data={
                "mapping_date": "date",
                "mapping_pilot": "crew_name",
                "mapping_from": "departure_icao",
                "mapping_to": "arrival_icao",
                "mapping_flight time": "flight_time",
                "mapping_landings": "landing_count",
            },
            follow_redirects=False,
        )
        client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "1", "decision": f"overwrite:{existing_id}"},
        )

        with app.app_context():
            entry = db.session.get(Flight, existing_id)
            assert entry.pic_name == "New Pilot"

    def test_resolve_new_creates_entry_with_pic_name(self, app, client):
        """Cover the pic_name-assignment branch of the 'new' decision."""
        _uid, tid = _create_user_and_tenant(app, email="arv24@example.com")
        acid = _add_aircraft(app, tid, registration="OO-ARV24")
        self._seed_near_match(app, acid)
        _login(app, client, email="arv24@example.com")

        csv_text = "Date,Pilot,From,To,Flight time,Landings\n2020-05-01,Some Pilot,EBOS,EBBR,1.4,2\n"
        _upload(client, acid, csv_text=csv_text)
        client.post(
            f"/aircraft/{acid}/flights/import/execute",
            data={
                "mapping_date": "date",
                "mapping_pilot": "crew_name",
                "mapping_from": "departure_icao",
                "mapping_to": "arrival_icao",
                "mapping_flight time": "flight_time",
                "mapping_landings": "landing_count",
            },
            follow_redirects=False,
        )
        rv = client.post(
            f"/aircraft/{acid}/flights/import/review/resolve",
            data={"row_num": "1", "decision": "new"},
        )
        assert rv.status_code == 302

        with app.app_context():
            new_entry = (
                Flight.query.filter_by(aircraft_id=acid)
                .order_by(Flight.id.desc())
                .first()
            )
            assert new_entry.pic_name == "Some Pilot"
