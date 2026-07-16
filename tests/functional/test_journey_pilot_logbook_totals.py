"""J7 — Pilot logbook totals from mixed sources (docs/functional_test_plan.md).

Intent: the EASA totals row and the dashboard's passenger-currency panel
reflect everything the pilot logged, whatever the entry path (linked
flight, standalone manual entry, FSTD session).

Existing: test_pilot_logbook.py and test_pilot_currency.py each cover
their half with direct-model setup; this drives all three entry paths
through their real routes and reads the rendered totals row + currency
panel, in one journey.

Deviation from the plan's suggested "backdate via direct write": passing
the intended date straight into each creation route achieves the same
90-day-window setup with no direct model access at all -- `log_flight`'s
`date` field sets FlightEntry.date, and the auto-linked PilotLogbookEntry
copies it from there at creation time (app/flights/routes.py's
apply_linked_pilot_entry), so there is nothing left to backdate
after the fact.
"""

import re
from datetime import date, timedelta

from tests.functional.conftest import log_flight, submit

# Standalone manual entry, well within the 90-day window but alone short of
# the 3-landing passenger-currency requirement (FCL.060(b)(1)).
_STANDALONE_DATE = (date.today() - timedelta(days=45)).isoformat()
# FSTD session: contributes flight time to its own column only, no landings.
_FSTD_DATE = (date.today() - timedelta(days=30)).isoformat()
# The flight logged through the form today is the one that crosses the
# passenger-currency boundary: 1 (standalone) + 2 (this flight) = 3.
_FLIGHT_DATE = date.today().isoformat()

_FLIGHT_HOURS = 1.5  # counters 1000.0 -> 1001.5, pilot_role=pic
_STANDALONE_SE = 1.0
_FSTD_DURATION = 1.5


def _tfoot_cells(html: bytes) -> list[str]:
    tfoot = html.split(b"<tfoot>")[1].split(b"</tfoot>")[0]
    return [
        c.decode()
        for c in re.findall(rb"<td>(?:<strong>)?(.*?)(?:</strong>)?</td>", tfoot)
    ]


def test_pilot_logbook_totals_from_mixed_sources(owner_env, app):
    client = owner_env.client
    aircraft_id = owner_env.aircraft_id

    # Create a PilotProfile via a real (empty) profile save -- the GET
    # handler also auto-vivifies one, but only flush()es it (no commit),
    # so it doesn't survive past that request; the dashboard's currency
    # card is hidden entirely without a persisted profile.
    submit(client, "/pilot/profile", {})

    # Standalone manual entry: 1 day landing, single_pilot_se 1.0h.
    submit(
        client,
        "/pilot/logbook/new",
        {
            "entry_type": "flight",
            "date": _STANDALONE_DATE,
            "aircraft_type": "Cessna 172S",
            "aircraft_registration": "OO-TST",
            "pic_name": "Test Pilot",
            "single_pilot_se": str(_STANDALONE_SE),
            "function_pic": str(_STANDALONE_SE),
            "landings_day": "1",
            "landings_night": "0",
        },
    )

    # FSTD session: only fstd_duration, no landings, no flight-time columns.
    submit(
        client,
        "/pilot/logbook/new",
        {
            "entry_type": "fstd",
            "date": _FSTD_DATE,
            "fstd_type": "FNPT",
            "fstd_duration": str(_FSTD_DURATION),
        },
    )

    # Before the flight below: only 1 qualifying landing in the window ->
    # short of the 3 required -> "Not current", not "No data" (an entry
    # does qualify, it's just not enough).
    dash = client.get("/")
    assert b"Not current" in dash.data
    assert b"1 / 3 landings (need 2 more)" in dash.data

    # Linked flight via the unified form: PIC, night 1.0, 2 night landings.
    log_flight(
        client,
        app,
        aircraft_id,
        date=_FLIGHT_DATE,
        pilot_role="pic",
        night_time=str(_FLIGHT_HOURS - 0.5),
        landings_day="0",
        landings_night="2",
        flight_time_counter_start="1000.0",
        flight_time_counter_end=str(1000.0 + _FLIGHT_HOURS),
    )

    # Passenger currency now crosses the boundary: 1 (standalone) + 2 (this
    # flight) = 3, exactly the FCL.060(b)(1) requirement.
    dash = client.get("/")
    assert b"Valid" in dash.data
    assert b"3 landings / 90 days" in dash.data

    logbook = client.get("/pilot/logbook")
    cells = _tfoot_cells(logbook.data)
    # Column order per pilots/logbook.html's <tfoot>: landings_day,
    # landings_night, single_pilot_se, single_pilot_me, night_time,
    # multi_pilot, fstd_duration, total_flight_time, night_time (dup),
    # instrument_time, function_pic, function_copilot, function_dual,
    # function_instructor.
    assert cells == [
        "1",  # landings_day: standalone only
        "2",  # landings_night: flight only
        "2.5",  # single_pilot_se: 1.0 (standalone) + 1.5 (flight)
        "—",  # single_pilot_me: none
        "1",  # night_time: 1.0 (flight only)
        "—",  # multi_pilot: none
        "1.5",  # fstd_duration: FSTD entry only -- not folded into flight time
        "2.5",  # total_flight_time: se + me + multi_pilot, excludes FSTD
        "1",  # night_time (duplicated column)
        "—",  # instrument_time: none
        "2.5",  # function_pic: 1.0 (standalone) + 1.5 (flight, pilot_role=pic)
        "—",  # function_copilot: none
        "—",  # function_dual: none
        "—",  # function_instructor: none
    ]
