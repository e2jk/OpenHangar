"""Fail-closed route capabilities for passport-only tenant memberships.

Role-less memberships never inherit VIEWER or a user's global pilot/maintenance
flags. Add endpoints deliberately, after checking their own-data boundaries.
"""

from flask import abort, redirect, request, session, url_for
from flask.typing import ResponseReturnValue
from models import TenantUser
from utils import current_membership

PASSPORT_ENDPOINTS = frozenset(
    {
        "passports.index",
        "passports.export_account",
        "passports.new_passport",
        "passports.edit_passport",
        "passports.next_edition",
        "passports.detail",
        "passports.print_passport",
        "passports.discussion",
        "passports.target_detail",
        "passports.where_next",
        "passports.stamp_photo",
        "passports.voucher_ledger",
        "passports.series_detail",
        "passports.group_settings",
        "passports.group_members",
        "passports.invitation_settings",
        "passports.join_passport",
        "passports.register_passport",
        "passports.verify_passport_email",
        "passports.archive_passport",
        "passports.delete_passport",
        "passports.link_series",
        "passports.start_logbook",
    }
)
# A personal pilot logbook ('logbook' scope): only the account's own entries.
# Imports, GPS-import review, tracks, minimums and the pilot profile stay out
# until each has been checked for tenant data; aircraft flights stay out too.
LOGBOOK_ENDPOINTS = frozenset(
    {
        "pilots.logbook",
        "pilots.new_entry",
        "pilots.edit_entry",
        "pilots.delete_entry",
        "pilots.view_entry",
        "pilots.pilot_stats",
        "flights.parse_gps_api",
        "airport_search",
        "aircraft_type_search",
    }
)
ACCOUNT_ENDPOINTS = frozenset(
    {
        "static",
        "auth.login",
        "auth.logout",
        "auth.profile",
        "auth.reset_password",
        "config.notification_preferences",
        "notifications.snooze",
        "set_language",
        "set_theme",
        "pwa_manifest",
        "service_worker",
        "favicon",
        "robots",
        "health",
        "api_banners",
    }
)


def restricted(membership: TenantUser | None) -> bool:
    return membership is not None and membership.access_scope != "full"


def personal_logbook(membership: TenantUser | None) -> bool:
    return membership is not None and membership.access_scope == "logbook"


def enforce() -> ResponseReturnValue | None:
    if (
        request.endpoint == "static"
        or not session.get("user_id")
        or not restricted(current_membership())
    ):
        return None
    if request.endpoint == "index":
        return redirect(url_for("passports.index"))
    allowed = PASSPORT_ENDPOINTS | ACCOUNT_ENDPOINTS
    if personal_logbook(current_membership()):
        allowed |= LOGBOOK_ENDPOINTS
    if request.endpoint not in allowed:
        abort(403)
    return None
