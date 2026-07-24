"""J8 — GPS import round trip (docs/functional_test_plan.md).

Intent: a GPS file becomes exactly one flight + one pilot entry + one
track, and importing it twice — confirming both times, not just the
second time discarded — does not duplicate anything.

Existing: test_gps_import.py/test_pilot_gps_import.py cover parsing and
a single-pass import; the re-upload-and-confirm-again journey that keeps
counts at exactly one is new. This used to be a real bug (confirming a
re-matched segment always created a brand-new GpsTrack, orphaning the
old one, and unconditionally created a second PilotLogbookEntry for the
same flight) — see the exact-duplicate/near-match fixes in
_gps_import_create_segment (app/aircraft/routes.py) for the FlightEntry
review logic that made this fixable in the first place.
"""

from pathlib import Path

from models import (  # pyright: ignore[reportMissingImports]
    FlightEntry,
    GpsTrack,
    PilotLogbookEntry,
    db,
)

from tests.functional.conftest import submit

_FIXTURE = Path(__file__).parent.parent / "e2e" / "fixtures" / "test_flight.gpx"


def test_gps_import_round_trip_no_duplicates(owner_env, app):
    client = owner_env.client
    aircraft_id = owner_env.aircraft_id

    with open(_FIXTURE, "rb") as fh:
        submit(
            client,
            f"/aircraft/{aircraft_id}/gps-import",
            {"gps_files": (fh, "test_flight.gpx")},
            content_type="multipart/form-data",
        )

    # Confirm the single parsed segment as a PIC flight.
    submit(
        client,
        f"/aircraft/{aircraft_id}/gps-import/confirm-one",
        {
            "seg_idx": "0",
            "pilot_role": "pic",
            "dep_icao": "EBBR",
            "arr_icao": "LFPG",
        },
    )

    with app.app_context():
        assert FlightEntry.query.filter_by(aircraft_id=aircraft_id).count() == 1
        assert PilotLogbookEntry.query.count() == 1
        assert GpsTrack.query.count() == 1

    logbook = client.get("/pilot/logbook")
    assert b"bi-geo-alt" in logbook.data

    with app.app_context():
        flight_before = FlightEntry.query.filter_by(aircraft_id=aircraft_id).one()
        counters_before = (
            flight_before.flight_time_counter_start,
            flight_before.flight_time_counter_end,
        )

    # Re-upload the identical file: the parsed segment's block times are
    # unchanged, so review's +/-15-minute overlap check matches it to the
    # flight just created (routes.py's _BLOCK_TOLERANCE) -- this is the
    # "duplicate" the plan refers to, not a file-hash comparison.
    with open(_FIXTURE, "rb") as fh:
        review = submit(
            client,
            f"/aircraft/{aircraft_id}/gps-import",
            {"gps_files": (fh, "test_flight.gpx")},
            content_type="multipart/form-data",
        )
    assert b"Matches existing flight" in review.data

    with app.app_context():
        old_track_id = (
            FlightEntry.query.filter_by(aircraft_id=aircraft_id).one().gps_track_id
        )

    # Confirm as-is (not skip) — the matched-flight path must update the
    # existing FlightEntry/PilotLogbookEntry/GpsTrack in place rather than
    # creating a second copy of any of them.
    submit(
        client,
        f"/aircraft/{aircraft_id}/gps-import/confirm-one",
        {
            "seg_idx": "0",
            "pilot_role": "pic",
            "dep_icao": "EBBR",
            "arr_icao": "LFPG",
        },
    )

    with app.app_context():
        assert FlightEntry.query.filter_by(aircraft_id=aircraft_id).count() == 1
        assert PilotLogbookEntry.query.count() == 1
        assert GpsTrack.query.count() == 1
        # The superseded track was deleted, not left orphaned.
        assert db.session.get(GpsTrack, old_track_id) is None
        new_track_id = (
            FlightEntry.query.filter_by(aircraft_id=aircraft_id).one().gps_track_id
        )
        assert new_track_id != old_track_id

    logbook_after = client.get("/pilot/logbook")
    assert b"bi-geo-alt" in logbook_after.data

    with app.app_context():
        flight_after = FlightEntry.query.filter_by(aircraft_id=aircraft_id).one()
        counters_after = (
            flight_after.flight_time_counter_start,
            flight_after.flight_time_counter_end,
        )
    assert counters_after == counters_before
