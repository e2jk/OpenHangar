import hashlib
import hmac
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, abort, redirect, render_template, request, session, url_for

from models import DemoSlot, db

log = logging.getLogger(__name__)

demo_bp = Blueprint("demo", __name__)

_DEFAULT_BUSY_WINDOW = 30


def _busy_window_minutes() -> int:
    try:
        return int(os.environ.get("DEMO_BUSY_WINDOW_MINUTES", _DEFAULT_BUSY_WINDOW))
    except ValueError:
        return _DEFAULT_BUSY_WINDOW


@demo_bp.route("/demo/enter", methods=["POST"])
def enter():
    # Restore existing slot if still valid
    existing_slot_id = session.get("demo_slot_id")
    if existing_slot_id:
        slot = db.session.get(DemoSlot, existing_slot_id)
        if slot:
            session["user_id"] = slot.user_id
            _touch_slot(slot)
            return redirect(url_for("index"))

    # Assign the least-recently-used slot
    slot = (
        DemoSlot.query
        .order_by(DemoSlot.last_activity_at.asc().nullsfirst())
        .first()
    )
    if slot is None:
        return redirect(url_for("index"))

    # If even the LRU slot is still warm, all slots are actively in use
    window = _busy_window_minutes()
    cutoff = datetime.utcnow() - timedelta(minutes=window)
    if slot.last_activity_at and slot.last_activity_at >= cutoff:
        return render_template("demo_full.html"), 503

    session.clear()
    session["demo_slot_id"] = slot.id
    session["user_id"] = slot.user_id
    session.permanent = True
    _touch_slot(slot)
    return redirect(url_for("index"))


def _touch_slot(slot: DemoSlot) -> None:
    slot.last_activity_at = datetime.now(timezone.utc)
    db.session.commit()


def demo_has_recent_activity(window_minutes: int = 20) -> bool:
    """Return True if any slot had activity within *window_minutes*."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    return DemoSlot.query.filter(DemoSlot.last_activity_at >= cutoff).count() > 0


@demo_bp.route("/demo/webhook", methods=["POST"])
def webhook():
    """
    Trigger a demo refresh immediately after a new image is published.

    GitHub Actions POSTs here with:
      Authorization: Bearer <DEMO_WEBHOOK_SECRET>

    The secret is compared using constant-time HMAC so timing attacks are not
    possible.  The refresh script is launched as a background subprocess; the
    response is returned before it completes.

    Returns 204 on success, 403 on bad/missing secret, 503 if the script path
    is not found (image bind-mount not in place).
    """
    secret = os.environ.get("DEMO_WEBHOOK_SECRET", "").strip()
    if not secret:
        log.warning("DEMO_WEBHOOK_SECRET not configured — rejecting webhook")
        abort(403)

    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()

    if not hmac.compare_digest(
        hashlib.sha256(token.encode()).digest(),
        hashlib.sha256(secret.encode()).digest(),
    ):
        log.warning("Webhook: invalid secret from %s", request.remote_addr)
        abort(403)

    # The refresh script is published to /refresh by the container entrypoint
    refresh_script = Path("/refresh/refresh.sh")
    if not refresh_script.exists():
        log.error("Webhook: refresh script not found at %s", refresh_script)
        abort(503)

    log.info("Webhook: valid request from %s — launching refresh", request.remote_addr)
    subprocess.Popen(
        [str(refresh_script)],
        stdout=open("/var/log/openhangar-demo.log", "a"),
        stderr=subprocess.STDOUT,
        close_fds=True,
    )
    return "", 204
