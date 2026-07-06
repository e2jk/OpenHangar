"""
Phase 32B — Full-data template smoke tests.

Each test exercises the positive branch of a {% if obj.optional_attr %} block
that the minimal fixtures in other test files never enter. All tests assert
status 200 so that a StrictUndefined failure surfaces as an error rather than
silently returning Undefined.

Gaps covered:
  1. aircraft/flight_detail.html  — entry.source == 'gps_import' and entry.gps_import_batch
  2. flights/flight_form.html     — flight.gps_track (edit form, GPS track already linked)
  3. pilots/entry_detail.html     — entry.flight_id and entry.flight
  4. pilots/entry_form.html       — entry.gps_track (edit form, GPS track already linked)
  5. aircraft/detail.html         — last_wb_entry.label (W&B entry with a label)
  6. flights/logbook_component.html — component.removed_at
"""

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from datetime import date, datetime, timezone

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AircraftGpsImportBatch,
    Component,
    FlightCrew,
    FlightEntry,
    GpsTrack,
    PilotLogbookEntry,
    Role,
    Tenant,
    TenantUser,
    User,
    WeightBalanceConfig,
    WeightBalanceEntry,
    db,
)


# ── Shared setup ──────────────────────────────────────────────────────────────


def _setup(app):
    """Create a tenant, OWNER user, and basic aircraft. Returns (user_id, aircraft_id)."""
    with app.app_context():
        tenant = Tenant(name="FD Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email="fd@example.com",
            password_hash=_pw_hash.hash("pw"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        ac = Aircraft(
            tenant_id=tenant.id,
            registration="OO-FDT",
            make="Piper",
            model="PA-28-181",
        )
        db.session.add(ac)
        db.session.commit()
        return user.id, ac.id


def _login(app, client, uid):
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def _make_flight(app, aircraft_id):
    with app.app_context():
        fe = FlightEntry(
            aircraft_id=aircraft_id,
            date=date(2024, 5, 1),
            departure_icao="EBOS",
            arrival_icao="EBBR",
            flight_time_counter_start=100.0,
            flight_time_counter_end=101.5,
        )
        db.session.add(fe)
        db.session.flush()
        db.session.add(
            FlightCrew(flight_id=fe.id, name="J. Smith", role="PIC", sort_order=0)
        )
        db.session.commit()
        return fe.id


# ── Gap 1: flight_detail — gps_import_batch link ─────────────────────────────


class TestFlightDetailWithGpsImportBatch:
    """aircraft/flight_detail.html:116 — entry.source == 'gps_import' and entry.gps_import_batch"""

    def test_renders_200_with_batch_link(self, app, client):
        uid, ac_id = _setup(app)
        _login(app, client, uid)

        with app.app_context():
            batch = AircraftGpsImportBatch(
                aircraft_id=ac_id,
                pilot_user_id=uid,
                source_filenames=["track.gpx"],
                imported_at=datetime.now(timezone.utc),
                format_detected="gpx",
                segments_found=1,
                segments_imported=1,
            )
            db.session.add(batch)
            db.session.flush()
            fe = FlightEntry(
                aircraft_id=ac_id,
                date=date(2024, 5, 1),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                flight_time_counter_start=100.0,
                flight_time_counter_end=101.5,
                source="gps_import",
                gps_import_batch_id=batch.id,
            )
            db.session.add(fe)
            db.session.flush()
            db.session.add(
                FlightCrew(flight_id=fe.id, name="J. Smith", role="PIC", sort_order=0)
            )
            db.session.commit()
            fe_id = fe.id

        resp = client.get(f"/aircraft/{ac_id}/flights/{fe_id}")
        assert resp.status_code == 200


# ── Gap 2: flight edit form — gps_track already linked ───────────────────────


class TestFlightEditFormWithGpsTrack:
    """flights/flight_form.html:116-119 — flight.gps_track (and source_filename)"""

    def test_renders_200_with_gps_track_linked(self, app, client):
        uid, ac_id = _setup(app)
        _login(app, client, uid)

        with app.app_context():
            track = GpsTrack(
                source_filename="flight.gpx",
                geojson={"type": "FeatureCollection", "features": []},
            )
            db.session.add(track)
            db.session.flush()
            fe = FlightEntry(
                aircraft_id=ac_id,
                date=date(2024, 5, 1),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                flight_time_counter_start=100.0,
                flight_time_counter_end=101.5,
                gps_track_id=track.id,
            )
            db.session.add(fe)
            db.session.flush()
            db.session.add(
                FlightCrew(flight_id=fe.id, name="J. Smith", role="PIC", sort_order=0)
            )
            db.session.commit()
            fe_id = fe.id

        resp = client.get(f"/flights/{fe_id}/edit")
        assert resp.status_code == 200
        assert b"GPS track linked" in resp.data
        assert b"flight.gpx" in resp.data


# ── Gap 3: pilot logbook entry_detail — entry.flight linked ──────────────────


class TestPilotEntryDetailWithLinkedFlight:
    """pilots/entry_detail.html:140 — entry.flight_id and entry.flight"""

    def test_renders_200_with_flight_linked(self, app, client):
        uid, ac_id = _setup(app)
        _login(app, client, uid)

        fe_id = _make_flight(app, ac_id)

        with app.app_context():
            entry = PilotLogbookEntry(
                pilot_user_id=uid,
                flight_id=fe_id,
                date=date(2024, 5, 1),
                aircraft_type="PA28",
                aircraft_registration="OO-FDT",
                single_pilot_se=1.5,
                landings_day=1,
                function_pic=1.5,
            )
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id

        resp = client.get(f"/pilot/logbook/{entry_id}/view")
        assert resp.status_code == 200


# ── Gap 4: pilot logbook entry_form edit — gps_track already linked ──────────


class TestPilotEntryFormWithGpsTrack:
    """pilots/entry_form.html:42-44 — entry.gps_track (and source_filename)"""

    def test_renders_200_with_gps_track_linked(self, app, client):
        uid, ac_id = _setup(app)
        _login(app, client, uid)

        with app.app_context():
            track = GpsTrack(
                source_filename="pilot_track.gpx",
                geojson={"type": "FeatureCollection", "features": []},
            )
            db.session.add(track)
            db.session.flush()
            entry = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date(2024, 5, 1),
                aircraft_type="PA28",
                aircraft_registration="OO-FDT",
                single_pilot_se=1.5,
                landings_day=1,
                function_pic=1.5,
                gps_track_id=track.id,
            )
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id

        resp = client.get(f"/pilot/logbook/{entry_id}/edit")
        assert resp.status_code == 200
        assert b"GPS track linked" in resp.data
        assert b"pilot_track.gpx" in resp.data


# ── Gap 5: aircraft detail — last_wb_entry.label ─────────────────────────────


class TestAircraftDetailWithLabelledWbEntry:
    """aircraft/detail.html:466 — last_wb_entry.label"""

    def test_renders_200_with_wb_entry_label(self, app, client):
        uid, ac_id = _setup(app)
        _login(app, client, uid)

        with app.app_context():
            cfg = WeightBalanceConfig(
                aircraft_id=ac_id,
                empty_weight=580,
                empty_cg_arm=2.20,
                max_takeoff_weight=1100,
                forward_cg_limit=2.00,
                aft_cg_limit=2.40,
            )
            db.session.add(cfg)
            db.session.flush()
            wb = WeightBalanceEntry(
                config_id=cfg.id,
                date=date(2024, 5, 1),
                label="Solo cross-country",
                total_weight=900,
                loaded_cg=2.15,
                is_in_envelope=True,
                station_weights={},
            )
            db.session.add(wb)
            db.session.commit()

        resp = client.get(f"/aircraft/{ac_id}")
        assert resp.status_code == 200
        assert b"Solo cross-country" in resp.data


# ── Gap 6: component logbook — component.removed_at ──────────────────────────


class TestComponentLogbookWithRemovedAt:
    """flights/logbook_component.html:70 — component.removed_at"""

    def test_renders_200_with_removed_component(self, app, client):
        uid, ac_id = _setup(app)
        _login(app, client, uid)

        with app.app_context():
            comp = Component(
                aircraft_id=ac_id,
                make="Lycoming",
                model="O-360",
                type="engine",
                installed_at=date(2020, 1, 1),
                removed_at=date(2024, 1, 15),
                time_at_install=1200.0,
            )
            db.session.add(comp)
            db.session.commit()
            comp_id = comp.id

        resp = client.get(f"/aircraft/{ac_id}/components/{comp_id}/logbook")
        assert resp.status_code == 200
        assert b"2024-01-15" in resp.data
