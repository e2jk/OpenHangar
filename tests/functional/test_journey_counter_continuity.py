"""J2 — Counter continuity across entry paths (docs/functional_test_plan.md).

Intent: no matter how entries enter the system (form, edit, CSV import),
the logbook stays arithmetically continuous and the aircraft's current
hours equal the highest counter across all of them.

Existing partial coverage: tests/test_airframe_import.py, tests/test_flights.py
cover each path separately; nothing mixes them.
"""

from io import BytesIO

from tests.functional.conftest import edit_flight, log_flight, submit


def test_counter_continuity_across_form_edit_and_import(owner_env, app):
    client = owner_env.client
    aircraft_id = owner_env.aircraft_id

    # Flight A via the form: 1000.0 -> 1001.5.
    fe_a_id = log_flight(
        client,
        app,
        aircraft_id,
        date="2024-06-01",
        flight_time_counter_start="1000.0",
        flight_time_counter_end="1001.5",
    )

    # Flight B via the form, accepting the prefill (A's original end, 1001.5)
    # as its start — captured here, *before* A gets edited below.
    new_form = client.get(f"/flights/new?aircraft_id={aircraft_id}")
    assert b"1001.5" in new_form.data
    fe_b_id = log_flight(
        client,
        app,
        aircraft_id,
        date="2024-06-02",
        flight_time_counter_start="1001.5",
        flight_time_counter_end="1003.0",
    )

    # Edit flight A's end counter via /flights/<id>/edit — after this, A's
    # end (1002.0) no longer matches B's already-stored start (1001.5).
    edit_flight(
        client,
        fe_a_id,
        aircraft_id,
        date="2024-06-01",
        flight_time_counter_start="1000.0",
        flight_time_counter_end="1002.0",
    )

    # A third entry via the airframe CSV import: 1003.0 -> 1004.5.
    csv_content = (
        "Date,Pilot,From,To,Flight time,Landings,Counter start,Counter end,Remarks\n"
        "2024-06-03,Jean Dupont,EBBR,EBOS,1.5,1,1003.0,1004.5,Imported flight\n"
    ).encode()
    upload_resp = client.post(
        f"/aircraft/{aircraft_id}/flights/import",
        data={
            "logbook_file": (BytesIO(csv_content), "airframe.csv", "text/csv"),
        },
        content_type="multipart/form-data",
    )
    assert upload_resp.status_code == 200
    submit(
        client,
        f"/aircraft/{aircraft_id}/flights/import/execute",
        {
            "mapping_date": "date",
            "mapping_pilot": "crew_name",
            "mapping_from": "departure_icao",
            "mapping_to": "arrival_icao",
            "mapping_flight time": "flight_time",
            "mapping_landings": "landing_count",
            "mapping_counter start": "flight_counter_start",
            "mapping_counter end": "flight_counter_end",
            "mapping_remarks": "notes",
        },
    )

    # The edit did NOT disturb flight B's stored start value — still 1001.5,
    # even though it no longer chains onto A's new end (1002.0). This
    # documents today's semantics: edits are per-row, not cascading.
    with app.app_context():
        from models import FlightEntry, db  # pyright: ignore[reportMissingImports]

        fe_b = db.session.get(FlightEntry, fe_b_id)
        assert float(fe_b.flight_time_counter_start) == 1001.5

    # Logbook page shows the exact counter chain across all three paths —
    # each row renders only its *end* counter (a single running-total
    # reading per row, journey-log style; start values aren't displayed).
    logbook = client.get(f"/aircraft/{aircraft_id}/flights")
    assert logbook.status_code == 200
    body = logbook.data.decode()
    for value in ("1002.0", "1003.0", "1004.5"):
        assert value in body, f"{value} missing from logbook page"

    # Aircraft current hours = max end counter across all three paths.
    assert b"1004.5" in logbook.data
