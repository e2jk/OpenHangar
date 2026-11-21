"""'Start my pilot logbook' for a passport-only account, on the same account.

The account keeps its identity, visits and group memberships. The logbook
holds only its own flights: no role is granted, so no aircraft, reservation,
maintenance or other pilots' logbook access. The instance owner's
personal-logbook policy decides whether the account holder may start it, or
only an administrator (from the user list).
"""

import logging

from flask import abort, flash, redirect, render_template, request, session, url_for
from flask.typing import ResponseReturnValue
from flask_babel import gettext as _
from models import TenantProfile, User, db
from utils import _sl, current_membership, login_required

from passports import passports_bp

_log = logging.getLogger(__name__)


def self_start_allowed(tenant_id: int) -> bool:
    profile = TenantProfile.query.filter_by(tenant_id=tenant_id).first()
    return profile is not None and profile.personal_logbook_policy == "self"


@passports_bp.route("/start-logbook", methods=["GET", "POST"])
@login_required
def start_logbook() -> ResponseReturnValue:
    membership = current_membership()
    if membership is None:
        abort(403)
    if membership.access_scope != "passport":
        # Full accounts already have their logbook through their role, and a
        # personal logbook is already on.
        return redirect(url_for("pilots.logbook"))
    user = db.session.get(User, membership.user_id)
    allowed = (
        self_start_allowed(membership.tenant_id)
        and user is not None
        and not user.view_only
    )
    error = None
    if request.method == "POST":
        if not allowed:
            abort(403)
        if request.form.get("confirm") != "yes":
            error = _("Confirm that you want to start your pilot logbook.")
        else:
            membership.access_scope = "logbook"
            db.session.commit()
            _log.warning(
                "[SECURITY] passports.start_logbook user_id=%s tenant_id=%s ip=%s",
                _sl(str(session["user_id"])),
                _sl(str(membership.tenant_id)),
                _sl(request.remote_addr),
            )
            flash(
                _(
                    "Your pilot logbook is ready. Flights you add here also fill "
                    "your airfield passports."
                ),
                "success",
            )
            return redirect(url_for("pilots.logbook"))
    return render_template(
        "passports/start_logbook.html", allowed=allowed, error=error
    ), (422 if error else 200)
