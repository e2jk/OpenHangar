"""J10 — Notification day (docs/functional_test_plan.md).

Intent: when the daily loop runs, exactly the right people get exactly
the right emails, as a consequence of product state built through
routes, not direct model writes.

Existing: test_notifications.py unit-tests each `_check_*` in isolation;
the state-built-via-product, exact-recipient-set assertion is new.

Documented deviation (the plan's own "deviate only with a documented
reason" rule): the plan's "a second run the same day sends nothing new
(dedup behaviour)" does not hold against current code. There is no
dedup mechanism anywhere in services/notification_service.py or the
models it reads -- no NotificationLog/sent-at field, nothing keyed by
day. `run_daily_checks` recomputes each `_check_*` condition fresh from
current DB state every call; the only lock (advisory_lock_scope) guards
concurrent workers within one call, and is a no-op on SQLite anyway.
Asserting the mock's call count stays flat on a second run would assert
something false about the current product. Rather than silently drop
that half of the journey, this test asserts the real (duplicate-send)
behaviour explicitly, so it reads as a live finding: if dedup is ever
added, this assertion starts failing and needs updating right alongside
the fix -- it is not swept under the rug.
"""

from datetime import date, timedelta
from io import BytesIO
from unittest.mock import patch

from tests.functional.conftest import log_flight, second_user, submit

# Same due_engine_hours/interval_hours/formula as J3
# (test_journey_maintenance_lifecycle.py): warn = max(20*0.1, 5.0) = 5.0.
# Flying to 1006.0 leaves remaining = 4.0 <= 5.0 -> due_soon.
_DUE_HOURS = "1010.0"
_INTERVAL_HOURS = "20"
_HOBBS_AFTER_FLIGHT = "1006.0"


def test_notification_day_exact_recipients_and_no_dedup(owner_env, app, client_factory):
    owner = owner_env.client
    aircraft_id = owner_env.aircraft_id

    # An hours trigger pushed to due-soon by flying, not a direct write.
    submit(
        owner,
        f"/aircraft/{aircraft_id}/maintenance/new",
        {
            "name": "Oil change",
            "trigger_type": "hours",
            "due_engine_hours": _DUE_HOURS,
            "interval_hours": _INTERVAL_HOURS,
        },
    )
    log_flight(
        owner,
        app,
        aircraft_id,
        flight_time_counter_start="1000.0",
        flight_time_counter_end=_HOBBS_AFTER_FLIGHT,
        engine_time_counter_start="1000.0",
        engine_time_counter_end=_HOBBS_AFTER_FLIGHT,
    )

    # A document expiring well within the 30-day default threshold.
    submit(
        owner,
        f"/aircraft/{aircraft_id}/documents/upload",
        {
            "file": (BytesIO(b"%PDF-1.4 fake cert\n"), "cert.pdf"),
            "title": "Airworthiness cert",
            "valid_until": (date.today() + timedelta(days=7)).isoformat(),
        },
        content_type="multipart/form-data",
    )

    # Second user: role=maintenance, so they (a) are an eligible recipient
    # for both notification types (REQUIRED_CAPS is ["is_owner","is_maint"]
    # for each) and (b) can see + save a preference for them at all -- a
    # pure pilot/viewer wouldn't see either type on the real prefs page.
    maint_client = second_user(
        app, client_factory, owner, "maintenance", "maint@example.com", "Mo Maint"
    )
    # Opt out of document_expiring only: maintenance_due_soon must be
    # explicitly re-submitted as enabled, since the real form resubmits
    # every visible type on every POST (an omitted field parses as
    # disabled) -- there's no partial-update path.
    submit(
        maint_client,
        "/config/notifications/",
        {"enabled_maintenance_due_soon": "on"},
    )

    with patch("services.email_service.send_email") as mock_send:
        from services.notification_service import run_daily_checks

        run_daily_checks(app)

        def _calls():
            result = set()
            for call in mock_send.call_args_list:
                to = call.kwargs["to"]
                subject = call.kwargs["subject"]
                if "Maintenance due soon" in subject:
                    result.add((to, "maintenance_due_soon"))
                elif "Document expiring" in subject:
                    result.add((to, "document_expiring"))
            return result

        # Owner (ADMIN, default prefs) gets both; the maintenance user only
        # gets maintenance_due_soon, having opted out of document_expiring.
        assert _calls() == {
            (owner_env.email, "maintenance_due_soon"),
            (owner_env.email, "document_expiring"),
            ("maint@example.com", "maintenance_due_soon"),
        }
        first_run_count = mock_send.call_count

        # Second run, same day: with no dedup mechanism in the current
        # code (see module docstring), every condition is still true, so
        # every recipient is notified again -- this is today's actual
        # behaviour, asserted explicitly rather than assumed away.
        run_daily_checks(app)
        assert mock_send.call_count == first_run_count * 2
        assert _calls() == {
            (owner_env.email, "maintenance_due_soon"),
            (owner_env.email, "document_expiring"),
            ("maint@example.com", "maintenance_due_soon"),
        }
