"""J1 — First run to first flight (docs/functional_test_plan.md).

Intent: a fresh install can reach a working, correct instance without any
manual DB surgery — setup wizard, aircraft + engine component, one logged
flight, and the figures that depend on it all come from driving the
product through its HTTP surface.

Existing partial coverage: tests/test_onboarding_wizard.py (wizard alone),
tests/test_counters.py (hint helper alone) — neither chains them together.
"""

from tests.functional.conftest import log_flight

# Counters for the one flight logged in this journey.
_COUNTER_START = "1000.0"
_COUNTER_END = "1001.5"


def test_first_run_to_first_flight(owner_env, app):
    client = owner_env.client
    aircraft_id = owner_env.aircraft_id

    fe_id = log_flight(
        client,
        app,
        aircraft_id,
        flight_time_counter_start=_COUNTER_START,
        flight_time_counter_end=_COUNTER_END,
        engine_time_counter_start=_COUNTER_START,
        engine_time_counter_end=_COUNTER_END,
    )
    assert fe_id is not None

    # Dashboard: Aircraft.total_engine_hours is MAX(engine_time_counter_end)
    # across flights, rendered as "%.1f h" (app/templates/dashboard.html).
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert b"1001.5" in dashboard.data

    # Deviation from the plan's literal wording ("dashboard and aircraft
    # detail both show engine hours"): aircraft/detail.html does not render
    # any computed current-hours figure (no TBO configured here, so
    # component_hours() has nowhere to surface) — confirmed by reading the
    # template. The equivalent, actually-rendered figure is
    # Aircraft.total_flight_hours (MAX(flight_time_counter_end)) on the
    # airframe logbook page, so that's what this asserts instead.
    logbook = client.get(f"/aircraft/{aircraft_id}/flights")
    assert logbook.status_code == 200
    assert b"1001.5" in logbook.data
    assert b"EBOS" in logbook.data
    assert b"EBBR" in logbook.data

    # The next flight form pre-fills the flight/engine counter starts with
    # this flight's end value (_get_counter_hint, app/flights/routes.py).
    new_form = client.get(f"/flights/new?aircraft_id={aircraft_id}")
    assert new_form.status_code == 200
    assert b"1001.5" in new_form.data
