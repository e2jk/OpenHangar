"""
Tests for bulk import of a historical airframe logbook (CSV/Excel):
  - upload → column mapping (alias + saved-fingerprint proposals) → execute
  - FlightEntry + free-text FlightCrew creation, ICAO normalisation
  - counter-continuity warnings (never hard errors) and opening counters
  - batch rollback, role gating, and the failure paths
"""

import json
from datetime import date
from io import BytesIO

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from flights.airframe_import import (  # pyright: ignore[reportMissingImports]
    _clean_icao,
    airframe_type_hints,
    propose_airframe_mapping,
)
from models import (
    Aircraft,
    AirframeImportBatch,
    AirframeImportMapping,
    FlightCrew,
    FlightEntry,
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

    def test_execute_creates_entries_and_crew(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _upload(client, acid)
        resp = _execute(client, acid)
        assert resp.status_code == 302
        with app.app_context():
            entries = (
                FlightEntry.query.filter_by(aircraft_id=acid)
                .order_by(FlightEntry.date)
                .all()
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
            crew = FlightCrew.query.filter_by(flight_id=first.id).all()
            assert len(crew) == 1
            assert crew[0].name == "Jean Dupont"
            assert crew[0].user_id is None
            # Second row: lowercase + overlong places normalised, crew present
            second = entries[1]
            assert second.departure_icao == "EBBR"
            assert second.arrival_icao == "EBOS"
            # Third row: no pilot → no crew row, places default to ZZZZ
            third = entries[2]
            assert third.departure_icao == "ZZZZ"
            assert FlightCrew.query.filter_by(flight_id=third.id).count() == 0
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
                FlightEntry.query.filter_by(aircraft_id=acid)
                .order_by(FlightEntry.date)
                .first()
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
            entry = FlightEntry.query.filter_by(aircraft_id=acid).one()
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
            assert FlightEntry.query.filter_by(aircraft_id=acid).count() == 1

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
        assert (
            "more".encode() in resp.data
        )  # both details truncated with "… and N more"

    def test_rollback_removes_entries_crew_and_batch(self, app, client):
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
            assert FlightEntry.query.filter_by(aircraft_id=acid).count() == 0
            assert FlightCrew.query.count() == 0
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
        assert f"/aircraft/{acid}/flights/import" in resp.headers["Location"]

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
