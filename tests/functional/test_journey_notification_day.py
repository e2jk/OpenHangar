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


def test_arc_document_produces_single_email_and_supports_snooze(
    owner_env, app, client_factory
):
    """An ARC-type document upload must trigger exactly one email (the
    dedicated ARC_EXPIRY check), not also a generic DOCUMENT_EXPIRING one
    for the same underlying deadline. The email carries a one-click snooze
    link that suppresses future reminders until the ARC is renewed."""
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

    def _matching_calls(mock_send, needle):
        return [c for c in mock_send.call_args_list if needle in c.kwargs["subject"]]

    with patch("services.email_service.send_email") as mock_send:
        run_daily_checks(app)

        arc_calls = _matching_calls(mock_send, "ARC expiring")
        doc_calls = _matching_calls(mock_send, "Document expiring")
        assert len(arc_calls) == 1, "expected exactly one ARC_EXPIRY email"
        assert not doc_calls, "ARC document must not also fire DOCUMENT_EXPIRING"

        html_body = arc_calls[0].kwargs["html_body"]

    # Extract the snooze link from the rendered email body.
    import re

    match = re.search(r'href="([^"]*/notifications/snooze/[^"]+)"', html_body)
    assert match, "no snooze link found in the ARC reminder email"
    snooze_path = "/" + match.group(1).split("://", 1)[-1].split("/", 1)[-1]

    fresh_client = client_factory()
    get_resp = fresh_client.get(snooze_path)
    assert get_resp.status_code == 200
    post_resp = fresh_client.post(snooze_path)
    assert post_resp.status_code == 200

    # Re-running the daily check on a later "day" would still be
    # suppressed by the snooze (same expiry value) -- simulate by clearing
    # today's send log entry so only the snooze is under test here.
    with app.app_context():
        from models import (  # pyright: ignore[reportMissingImports]
            NotificationSendLog,
            db,
        )

        NotificationSendLog.query.delete()
        db.session.commit()

    with patch("services.email_service.send_email") as mock_send2:
        run_daily_checks(app)
        assert not _matching_calls(mock_send2, "ARC expiring")

    # Upload a renewed ARC with a different (but still within-threshold)
    # deadline -- the stale snooze, keyed to the old expiry value, must not
    # suppress the new cycle's reminder.
    submit(
        owner,
        f"/aircraft/{aircraft_id}/documents/upload",
        {
            "file": (BytesIO(b"%PDF-1.4 fake arc renewed\n"), "arc2.pdf"),
            "title": "Airworthiness Review Certificate (renewed)",
            "doc_type": "arc_certificate",
            "valid_until": (date.today() + timedelta(days=15)).isoformat(),
        },
        content_type="multipart/form-data",
    )
    with app.app_context():
        from models import (  # pyright: ignore[reportMissingImports]
            NotificationSendLog,
            db,
        )

        NotificationSendLog.query.delete()
        db.session.commit()

    with patch("services.email_service.send_email") as mock_send3:
        run_daily_checks(app)
        assert _matching_calls(mock_send3, "ARC expiring"), (
            "reminder must resume once the deadline actually changed"
        )
