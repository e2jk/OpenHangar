"""J10 — Notification day (docs/functional_test_plan.md).

Intent: when the daily loop runs, exactly the right people get exactly
the right emails, as a consequence of product state built through
routes, not direct model writes.

Existing: test_notifications.py unit-tests each `_check_*` in isolation;
the state-built-via-product, exact-recipient-set assertion is new.

A NotificationSendLog dedup keyed on (user, tenant, notification_type,
subject_ref, calendar day) now makes a same-day rerun of
`run_daily_checks` a no-op per recipient/instance -- added so a server
restart mid-day (e.g. to install a new version) does not resend
everything already sent earlier that day. See
services/notification_service.py `_already_sent_today`/`_record_sent`.
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

        # Second run, same day (e.g. a restart mid-day re-triggers the
        # scheduler): the send log means every condition is still true but
        # already-notified recipients are not emailed again.
        run_daily_checks(app)
        assert mock_send.call_count == first_run_count
        assert _calls() == {
            (owner_env.email, "maintenance_due_soon"),
            (owner_env.email, "document_expiring"),
            ("maint@example.com", "maintenance_due_soon"),
        }


def test_arc_document_produces_single_email(owner_env, app):
    """An ARC-type document upload must trigger exactly one email (the
    dedicated ARC_EXPIRY check), not also a generic DOCUMENT_EXPIRING one
    for the same underlying deadline."""
    owner = owner_env.client
    aircraft_id = owner_env.aircraft_id

    submit(
        owner,
        f"/aircraft/{aircraft_id}/documents/upload",
        {
            "file": (BytesIO(b"%PDF-1.4 fake arc\n"), "arc.pdf"),
            "title": "Airworthiness Review Certificate",
            "doc_type": "arc_certificate",
            "valid_until": (date.today() + timedelta(days=10)).isoformat(),
        },
        content_type="multipart/form-data",
    )

    from services.notification_service import run_daily_checks

    with patch("services.email_service.send_email") as mock_send:
        run_daily_checks(app)

        subjects = [c.kwargs["subject"] for c in mock_send.call_args_list]
        arc_calls = [s for s in subjects if "ARC expiring" in s]
        doc_calls = [s for s in subjects if "Document expiring" in s]
        assert len(arc_calls) == 1, "expected exactly one ARC_EXPIRY email"
        assert not doc_calls, "ARC document must not also fire DOCUMENT_EXPIRING"
