import os
from datetime import datetime, timedelta, timezone

from flask import Blueprint, redirect, render_template, session, url_for

from models import DemoSlot, db

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
