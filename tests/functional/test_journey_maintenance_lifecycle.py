"""J3 — Maintenance driven by flying (docs/functional_test_plan.md).

Intent: maintenance status is a consequence of actually flying, and
servicing resets the cycle — never touched by a direct model write.

Deviation from the plan's illustrative numbers (documented per the plan's
own "deviate only with a documented reason" rule): the due-soon threshold
is `remaining <= max(interval_hours * 0.1, 5.0)` (PilotLogbookEntry.status
sibling `MaintenanceTrigger.status`, app/models.py), not a flat "90 % of
interval used" rule as the plan's prose suggested. This test picks
due_engine_hours/interval_hours/flight counters that exercise the real
ok -> due_soon -> overdue transitions against that actual formula.

Existing partial coverage: tests/test_maintenance.py / test_fleet_maintenance.py
set counter values directly; no test advances hours by logging flights.
"""

from datetime import date, timedelta

from tests.functional.conftest import log_flight, submit

# due_engine_hours=1010.0, interval_hours=20.0 -> warn = max(20*0.1, 5.0) = 5.0
# ok:       remaining > 5   -> current_hobbs < 1005.0
# due_soon: 0 < remaining <= 5 -> 1005.0 <= current_hobbs < 1010.0
# overdue:  remaining <= 0  -> current_hobbs >= 1010.0
_DUE_HOURS = "1010.0"
_INTERVAL_HOURS = "20"


def _fly_to(client, app, aircraft_id, prev_hobbs, new_hobbs, day_offset):
    flight_date = (date(2024, 6, 1) + timedelta(days=day_offset)).isoformat()
    return log_flight(
        client,
        app,
        aircraft_id,
        date=flight_date,
        flight_time_counter_start=str(prev_hobbs),
        flight_time_counter_end=str(new_hobbs),
        engine_time_counter_start=str(prev_hobbs),
        engine_time_counter_end=str(new_hobbs),
    )


def test_maintenance_status_driven_by_flying(owner_env, app):
    client = owner_env.client
    aircraft_id = owner_env.aircraft_id

    # Create an hours trigger and a calendar trigger via the maintenance forms.
    submit(
        client,
        f"/aircraft/{aircraft_id}/maintenance/new",
        {
            "name": "Oil change",
            "trigger_type": "hours",
            "due_engine_hours": _DUE_HOURS,
            "interval_hours": _INTERVAL_HOURS,
        },
    )
    future_due = (date.today() + timedelta(days=200)).isoformat()
    submit(
        client,
        f"/aircraft/{aircraft_id}/maintenance/new",
        {
            "name": "Annual inspection",
            "trigger_type": "calendar",
            "due_date": future_due,
            "interval_days": "365",
        },
    )

    def statuses():
        ac_page = client.get(f"/aircraft/{aircraft_id}/maintenance")
        fleet_page = client.get("/maintenance")
        assert ac_page.status_code == 200
        assert fleet_page.status_code == 200
        return ac_page.data, fleet_page.data

    # Initial state (aircraft hours == install hours, 1000.0): both triggers ok.
    ac_body, fleet_body = statuses()
    assert b"OK" in ac_body
    assert b"Overdue" not in ac_body
    assert b"Due soon" not in ac_body

    # Fly to 1004.0 (remaining=6 > 5): still ok.
    _fly_to(client, app, aircraft_id, "1000.0", "1004.0", day_offset=0)
    ac_body, fleet_body = statuses()
    assert b"Due soon" not in ac_body
    assert b"Overdue" not in ac_body

    # Fly to 1006.0 (remaining=4 <= 5): due soon, on both pages.
    _fly_to(client, app, aircraft_id, "1004.0", "1006.0", day_offset=1)
    ac_body, fleet_body = statuses()
    assert b"Due soon" in ac_body
    assert b"Due soon" in fleet_body
    assert b"Overdue" not in ac_body

    # Fly past due (1011.0): overdue, on both pages.
    _fly_to(client, app, aircraft_id, "1006.0", "1011.0", day_offset=2)
    ac_body, fleet_body = statuses()
    assert b"Overdue" in ac_body
    assert b"Overdue" in fleet_body

    # Mark serviced: due_engine_hours becomes hobbs_at_service + interval
    # (1011.0 + 20.0 = 1031.0), status back to ok (remaining=20 > 5).
    with app.app_context():
        from models import MaintenanceTrigger  # pyright: ignore[reportMissingImports]

        trigger = MaintenanceTrigger.query.filter_by(
            aircraft_id=aircraft_id, name="Oil change"
        ).first()
        trigger_id = trigger.id

    submit(
        client,
        f"/aircraft/{aircraft_id}/maintenance/{trigger_id}/service",
        {"performed_at": "2024-06-04", "hobbs_at_service": "1011.0"},
    )

    with app.app_context():
        from models import MaintenanceTrigger  # pyright: ignore[reportMissingImports]

        refreshed = MaintenanceTrigger.query.filter_by(
            aircraft_id=aircraft_id, name="Oil change"
        ).first()
        assert float(refreshed.due_engine_hours) == 1031.0

    ac_body, fleet_body = statuses()
    assert b"OK" in ac_body
    assert b"Overdue" not in ac_body
    assert b"Due soon" not in ac_body

    # Backdate the calendar trigger to overdue via the maintenance edit form
    # (a real user would edit the due date, not just fly) — still driven
    # through the product, not a direct model write.
    with app.app_context():
        from models import MaintenanceTrigger  # pyright: ignore[reportMissingImports]

        calendar_trigger = MaintenanceTrigger.query.filter_by(
            aircraft_id=aircraft_id, name="Annual inspection"
        ).first()
        calendar_trigger_id = calendar_trigger.id

    past_due = (date.today() - timedelta(days=1)).isoformat()
    submit(
        client,
        f"/aircraft/{aircraft_id}/maintenance/{calendar_trigger_id}/edit",
        {
            "name": "Annual inspection",
            "trigger_type": "calendar",
            "due_date": past_due,
            "interval_days": "365",
        },
    )
    ac_body, fleet_body = statuses()
    assert b"Overdue" in ac_body
    assert b"Overdue" in fleet_body
