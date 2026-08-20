"""
Notifications blueprint — public one-click snooze links delivered in
reminder emails. No @login_required: the token itself is the credential,
same pattern as auth.reset_password / share.public_view.
"""

from datetime import UTC, datetime

from flask import (  # pyright: ignore[reportMissingImports]
    Blueprint,
    make_response,
    render_template,
    request,
)
from flask.typing import ResponseReturnValue  # pyright: ignore[reportMissingImports]
from models import NotificationSnooze, db  # pyright: ignore[reportMissingImports]

notifications_bp = Blueprint("notifications", __name__)


@notifications_bp.route("/notifications/snooze/<token>", methods=["GET", "POST"])
def snooze(token: str) -> ResponseReturnValue:
    """Confirm (POST) or preview (GET) snoozing one specific reminder
    instance until its underlying deadline value changes."""
    row = NotificationSnooze.query.filter_by(token=token).first_or_404()

    if request.method == "POST":
        row.snoozed_value = row.current_value
        row.snoozed_at = datetime.now(UTC)
        db.session.commit()

    resp = make_response(render_template("notifications/snooze.html", snooze=row))
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp
