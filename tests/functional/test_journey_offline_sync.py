"""J19 — Offline sync loop (docs/functional_test_plan.md).

Intent: snapshot -> concurrent edit via the normal form -> sync with a
stale base -> assert the per-field conflict payload -> resolve ->
assert final DB state.

Existing: test_offline_logbook.py (1738 lines) already covers the
snapshot shape, clean sync, and conflict detection/resolution
exhaustively at the route level -- but every "concurrent edit" there is
a direct SQLAlchemy mutation (`fe.nature_of_flight = "..."; commit()`),
never routed through the real flight-edit form. What this journey adds,
per the plan's own note ("chains them against the form"): the staleness
is produced by a real `POST /flights/<id>/edit` -- the same form
validation/duplicate-detection/milestone code path a browser user would
hit -- rather than a raw model write, chained end to end with the
snapshot/sync/resolve API.

There is no separate "resolve" endpoint (checked app/offline/routes.py
and the client-side app/static/js/offline_changes.js in full): a
resolution is just a second POST to the same sync endpoint, using the
conflict response's own `entry` as the new base (every field, not only
the conflicting one) and picking one side's value for whatever
conflicted -- exactly mirroring offline_changes.js's own resolve flow.
"""

from tests.functional.conftest import edit_flight, log_flight


def test_offline_sync_conflict_via_real_form_edit_then_resolves(owner_env, app):
    client = owner_env.client
    aircraft_id = owner_env.aircraft_id

    flight_id = log_flight(client, app, aircraft_id, nature_of_flight="Original value")

    # Snapshot: this is the "base" an offline client would have cached.
    snapshot = client.get(f"/api/offline/aircraft/{aircraft_id}/logbook").get_json()
    entry = next(e for e in snapshot["entries"] if e["id"] == flight_id)
    base = entry["fields"]
    assert base["nature_of_flight"] == "Original value"

    # Concurrent edit via the real online form -- not a direct model
    # write -- makes the cached base stale for nature_of_flight.
    edit_flight(client, flight_id, aircraft_id, nature_of_flight="Server value")

    # Offline sync: the client only changed nature_of_flight, to a THIRD
    # value neither matching its own stale base nor the server's current
    # value -- the exact shape sync_flight's conflict check requires.
    offline_fields = dict(base)
    offline_fields["nature_of_flight"] = "Local value"
    sync_resp = client.post(
        f"/api/offline/flights/{flight_id}/sync",
        json={"fields": offline_fields, "base": base},
    )
    assert sync_resp.status_code == 409
    payload = sync_resp.get_json()
    assert payload["status"] == "conflict"
    assert payload["conflicts"] == [
        {
            "field": "nature_of_flight",
            "base": "Original value",
            "local": "Local value",
            "server": "Server value",
        }
    ]

    # Nothing was committed -- a conflict on one field blocks the whole write.
    with app.app_context():
        from models import FlightEntry, db  # pyright: ignore[reportMissingImports]

        assert (
            db.session.get(FlightEntry, flight_id).nature_of_flight == "Server value"
        )  # unchanged by the rejected sync

    # Resolve: same endpoint, second call. The conflict response's own
    # `entry` becomes the new base for every field (mirroring
    # offline_changes.js's resolve flow exactly), with the one
    # conflicting field overridden to whichever side was picked -- here,
    # keeping the offline (local) value.
    resolved_fields = dict(payload["entry"])
    resolved_fields["nature_of_flight"] = "Local value"
    resolve_resp = client.post(
        f"/api/offline/flights/{flight_id}/sync",
        json={"fields": resolved_fields, "base": payload["entry"]},
    )
    assert resolve_resp.status_code == 200
    assert resolve_resp.get_json()["status"] == "ok"

    with app.app_context():
        from models import FlightEntry, db  # pyright: ignore[reportMissingImports]

        assert db.session.get(FlightEntry, flight_id).nature_of_flight == "Local value"
