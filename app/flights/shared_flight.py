"""Editing a flight that is in more than one pilot's logbook.

One Flight row is shared by both crew slots. The EASA figures and flight
details on it ("shared fields") describe the flight itself, so they have a
single owner: the pilot who logged it (``Flight.created_by_user_id``) while
they are still linked to a slot — plus tenant owners/admins for a managed
aircraft, who keep the aircraft log correct. Each slot's own name, role,
function hours and personal remark ("personal fields") belong to the pilot
in that slot alone.

- ``can_edit_shared`` decides who may use the full flight form / offline
  sync; everyone else linked to the flight is sent to the "my part of this
  flight" page (``flights.crew_entry``) to edit their personal fields and
  *suggest* corrections to shared ones (``FlightCorrectionSuggestion``).
- ``protect_other_pilots`` / ``restore_other_pilots`` wrap every shared-field
  save so it can never overwrite another linked pilot's personal fields.
- ``notify_shared_changes`` e-mails the other linked pilots what changed.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from flask import url_for  # pyright: ignore[reportMissingImports]
from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]
from flask_babel import lazy_gettext as _l  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    CorrectionStatus,
    CrewRole,
    CrewSlot,
    Flight,
    FlightCorrectionSuggestion,
    NotificationType,
    Role,
    TenantUser,
    User,
    db,
)
from offline.serialize import (  # pyright: ignore[reportMissingImports]
    _fmt_decimal,
    _fmt_int,
    _fmt_str,
    _fmt_time,
)
from pilots.form_parsing import (  # pyright: ignore[reportMissingImports]
    _parse_date,
    _parse_decimal,
    _parse_int,
    _parse_time,
)
from sqlalchemy import or_  # pyright: ignore[reportMissingImports]

log = logging.getLogger(__name__)

# ── Who may edit what ──────────────────────────────────────────────────────────


def shared_editor_ids(fe: Flight) -> set[int]:
    """Linked pilots allowed to edit the shared fields: the logger while still
    linked, otherwise (logger gone, or not tracked) every linked pilot."""
    linked = {u for u in (fe.pic_user_id, fe.second_crew_user_id) if u is not None}
    if fe.created_by_user_id in linked:
        return {fe.created_by_user_id}
    return linked


def can_edit_shared(fe: Flight, user_id: int | None, role: Any) -> bool:
    """Whether *user_id* may change *fe*'s shared fields. Users not linked to
    the flight keep the normal (tenant/aircraft) access rules."""
    if fe.slot_for(user_id) is None:
        return True
    if fe.aircraft_id is not None and role in (Role.ADMIN, Role.OWNER):
        return True
    return user_id in shared_editor_ids(fe)


_SLOT_PERSONAL_FIELDS: dict[str, tuple[str, ...]] = {
    CrewSlot.PIC: ("pic_name", "function_pic", "pic_remarks"),
    CrewSlot.SECOND: (
        "second_crew_name",
        "second_crew_role",
        "function_copilot",
        "function_dual",
        "function_instructor",
        "second_crew_remarks",
    ),
}
_FUNCTION_FIELDS = {
    "function_pic",
    "function_copilot",
    "function_dual",
    "function_instructor",
}
# Which function_* column holds the second-crew slot's hours, per role.
SECOND_CREW_FUNCTION_FIELD: dict[str, str] = {
    CrewRole.IP: "function_instructor",
    CrewRole.COPILOT: "function_copilot",
    CrewRole.STUDENT: "function_dual",
}


def _flight_total(fe: Flight) -> Decimal | None:
    if fe.flight_time is not None:
        return Decimal(str(fe.flight_time))
    total = fe.total_flight_time
    return Decimal(str(total)) if total is not None else None


def protect_other_pilots(fe: Flight, user_id: int | None) -> dict[str, Any]:
    """Snapshot the personal fields of every slot linked to someone other
    than *user_id* (``None`` → every linked slot), before a shared save."""
    slots: dict[str, dict[str, Any]] = {}
    for slot in CrewSlot.ALL:
        occupant = fe.pic_user_id if slot == CrewSlot.PIC else fe.second_crew_user_id
        if occupant is not None and occupant != user_id:
            slots[slot] = {f: getattr(fe, f) for f in _SLOT_PERSONAL_FIELDS[slot]}
    return {"total": _flight_total(fe), "slots": slots}


def restore_other_pilots(fe: Flight, snapshot: dict[str, Any]) -> None:
    """Undo whatever a shared save did to the protected personal fields.
    Function hours that simply matched the old flight time follow the new
    one, so a corrected flight time doesn't leave stale hours behind."""
    new_total = _flight_total(fe)
    old_total = snapshot["total"]
    for values in snapshot["slots"].values():
        for field, value in values.items():
            if (
                field in _FUNCTION_FIELDS
                and value is not None
                and old_total is not None
                and new_total is not None
                and Decimal(str(value)) == old_total
            ):
                value = new_total
            setattr(fe, field, value)


# ── Shared field specs (canonical strings) ────────────────────────────────────

# field → (kind, label). Kinds: date | time | decimal | int | text | reg.
FIELD_SPECS: dict[str, tuple[str, Any]] = {
    "date": ("date", _l("Date")),
    "other_aircraft_registration": ("reg", _l("Registration")),
    "other_aircraft_type": ("text", _l("Aircraft type")),
    "departure_icao": ("text", _l("Departure")),
    "arrival_icao": ("text", _l("Arrival")),
    "departure_time": ("time", _l("Departure time")),
    "arrival_time": ("time", _l("Arrival time")),
    "takeoff_time": ("time", _l("Takeoff time")),
    "landing_time": ("time", _l("Landing time")),
    "flight_time": ("decimal", _l("Flight time")),
    "single_pilot_se": ("decimal", _l("S/E time")),
    "single_pilot_me": ("decimal", _l("M/E time")),
    "landings_day": ("int", _l("Day landings")),
    "landings_night": ("int", _l("Night landings")),
    "night_time": ("decimal", _l("Night time")),
    "instrument_time": ("decimal", _l("Instrument time")),
    "multi_pilot": ("decimal", _l("Multi-pilot time")),
    "nature_of_flight": ("text", _l("Nature of flight")),
    "notes": ("text", _l("Remarks")),
}


def _canonical(kind: str, value: Any) -> str:
    if kind == "date":
        return value.isoformat() if value else ""
    if kind == "time":
        return _fmt_time(value)
    if kind == "decimal":
        return _fmt_decimal(value, 1)
    if kind == "int":
        return _fmt_int(value)
    return _fmt_str(value)


def _parse(kind: str, raw: str, label: str) -> tuple[Any, str | None]:
    if kind == "date":
        value, err = _parse_date(raw, label)
        if err is None and value is None:
            return None, _("Date is required.")
        return value, err
    if kind == "time":
        return _parse_time(raw, label)
    if kind == "decimal":
        return _parse_decimal(raw, label)
    if kind == "int":
        return _parse_int(raw, label)
    text = raw.strip()
    if kind == "reg":
        text = text.upper()
    return text or None, None


def shared_snapshot(fe: Flight) -> dict[str, str]:
    """Canonical values of every shared field, for change notifications.
    The aircraft is described by its display registration/type, so a
    managed aircraft reads the same way as a standalone one."""
    values = {
        f: _canonical(kind, getattr(fe, f)) for f, (kind, _lbl) in FIELD_SPECS.items()
    }
    values["other_aircraft_registration"] = _fmt_str(fe.display_registration)
    values["other_aircraft_type"] = _fmt_str(fe.display_aircraft_type)
    return values


def suggestable_fields(fe: Flight) -> list[str]:
    """Shared fields a pilot may propose new values for. On a managed
    aircraft, times and flight time derive from counters the logger keeps
    consistent, so they're left to the logger."""
    if fe.aircraft_id is not None:
        return [
            "date",
            "departure_icao",
            "arrival_icao",
            "landings_day",
            "landings_night",
            "night_time",
            "instrument_time",
            "multi_pilot",
            "nature_of_flight",
            "notes",
        ]
    time_fields = (
        ["flight_time"]
        if fe.flight_time is not None
        else ["single_pilot_se", "single_pilot_me"]
    )
    return [
        "date",
        "other_aircraft_registration",
        "other_aircraft_type",
        "departure_icao",
        "arrival_icao",
        "departure_time",
        "arrival_time",
        "takeoff_time",
        "landing_time",
        *time_fields,
        "landings_day",
        "landings_night",
        "night_time",
        "instrument_time",
        "multi_pilot",
        "notes",
    ]


def field_label(field: str) -> str:
    return str(FIELD_SPECS[field][1]) if field in FIELD_SPECS else field


def current_values(fe: Flight) -> dict[str, str]:
    return {f: _canonical(FIELD_SPECS[f][0], getattr(fe, f)) for f in FIELD_SPECS}


# ── Personal fields ("my part of this flight") ────────────────────────────────


def apply_personal_fields(fe: Flight, slot: str, form: Any) -> list[str]:
    """Validate and apply the slot occupant's own name, role (second crew),
    function hours and remark. Returns validation errors (nothing applied)."""
    errors: list[str] = []
    name = (form.get("name") or "").strip()
    if not name:
        errors.append(_("Name is required."))
    hours, err = _parse_decimal(form.get("function_hours") or "", _("Function time"))
    if err:
        errors.append(err)
    role = (form.get("role") or "").strip()
    if slot == CrewSlot.SECOND and role not in (
        CrewRole.IP,
        CrewRole.COPILOT,
        CrewRole.STUDENT,
        CrewRole.SP,
    ):
        errors.append(_("Please choose your role on this flight."))
    if errors:
        return errors

    remark = (form.get("remark") or "").strip() or None
    if slot == CrewSlot.PIC:
        fe.pic_name = name
        fe.function_pic = hours
        fe.pic_remarks = remark
    else:
        fe.second_crew_name = name
        fe.second_crew_role = role
        fe.function_copilot = None
        fe.function_dual = None
        fe.function_instructor = None
        field = SECOND_CREW_FUNCTION_FIELD.get(role)
        if field:
            setattr(fe, field, hours)
        fe.second_crew_remarks = remark
    return []


def personal_function_hours(fe: Flight, slot: str) -> Any:
    if slot == CrewSlot.PIC:
        return fe.function_pic
    field = SECOND_CREW_FUNCTION_FIELD.get(fe.second_crew_role or "")
    return getattr(fe, field) if field else None


# ── Correction suggestions ────────────────────────────────────────────────────


def build_suggestion(fe: Flight, form: Any) -> tuple[dict[str, list[str]], list[str]]:
    """Compare the submitted values of the suggestable fields with *fe*.
    Returns ``(changes, errors)`` with changes as field → [old, new]."""
    changes: dict[str, list[str]] = {}
    errors: list[str] = []
    for field in suggestable_fields(fe):
        kind, label = FIELD_SPECS[field]
        value, err = _parse(kind, form.get(field) or "", str(label))
        if err:
            errors.append(err)
            continue
        old = _canonical(kind, getattr(fe, field))
        new = _canonical(kind, value)
        if old != new:
            changes[field] = [old, new]
    return changes, errors


def apply_suggestion(
    suggestion: FlightCorrectionSuggestion, actor_id: int
) -> dict[str, str]:
    """Apply an accepted suggestion to its flight, keeping every linked
    pilot's personal fields intact. Returns the shared snapshot from before
    the change (for ``notify_shared_changes``)."""
    fe = _suggestion_flight(suggestion)
    before = shared_snapshot(fe)
    protected = protect_other_pilots(fe, None)
    allowed = set(suggestable_fields(fe))
    changed = {f: new for f, (_old, new) in suggestion.changes.items() if f in allowed}
    for field, new in changed.items():
        kind, label = FIELD_SPECS[field]
        value, _err = _parse(kind, new, str(label))
        setattr(fe, field, value)
    if "flight_time" in changed:
        if fe.single_pilot_me is not None:
            fe.single_pilot_me = fe.flight_time
        else:
            fe.single_pilot_se = fe.flight_time
    if fe.aircraft_id is not None and (
        "landings_day" in changed or "landings_night" in changed
    ):
        fe.landing_count = (fe.landings_day or 0) + (fe.landings_night or 0)
    restore_other_pilots(fe, protected)
    suggestion.status = CorrectionStatus.ACCEPTED
    suggestion.responded_at = datetime.now(UTC)
    return before


def reject_suggestion(suggestion: FlightCorrectionSuggestion) -> None:
    suggestion.status = CorrectionStatus.REJECTED
    suggestion.responded_at = datetime.now(UTC)


def _suggestion_flight(suggestion: FlightCorrectionSuggestion) -> Flight:
    fe = db.session.get(Flight, suggestion.flight_id)
    assert fe is not None, "flight_id is a non-null CASCADE foreign key"
    return fe


def pending_suggestions_to_review(user_id: int) -> list[FlightCorrectionSuggestion]:
    """Pending suggestions on flights *user_id* owns the shared fields of."""
    candidates: list[FlightCorrectionSuggestion] = (
        FlightCorrectionSuggestion.query.join(
            Flight, Flight.id == FlightCorrectionSuggestion.flight_id
        )
        .filter(
            FlightCorrectionSuggestion.status == CorrectionStatus.PENDING,
            FlightCorrectionSuggestion.suggested_by_user_id != user_id,
            or_(Flight.pic_user_id == user_id, Flight.second_crew_user_id == user_id),
        )
        .order_by(FlightCorrectionSuggestion.created_at)
        .all()
    )
    return [
        s for s in candidates if user_id in shared_editor_ids(_suggestion_flight(s))
    ]


def pending_suggestions_by(
    user_id: int, fe: Flight
) -> list[FlightCorrectionSuggestion]:
    suggestions: list[FlightCorrectionSuggestion] = (
        FlightCorrectionSuggestion.query.filter_by(
            flight_id=fe.id,
            suggested_by_user_id=user_id,
            status=CorrectionStatus.PENDING,
        )
        .order_by(FlightCorrectionSuggestion.created_at)
        .all()
    )
    return suggestions


# ── Notifications ─────────────────────────────────────────────────────────────


def _tenant_of(user_id: int) -> int | None:
    tu = TenantUser.query.filter_by(user_id=user_id).first()
    return tu.tenant_id if tu else None


def _display_name(user_id: int | None) -> str:
    user = db.session.get(User, user_id) if user_id else None
    return user.display_name if user else "—"


def _route(fe: Flight) -> str:
    return f"{fe.departure_icao or '?'} → {fe.arrival_icao or '?'}"


def dispatch_to_user(
    user_id: int, notification_type: str, context: dict[str, Any]
) -> None:
    """Send one notification to one user, in their own tenant. Failures are
    logged, never raised — the change it reports is already saved."""
    from services.notification_service import (  # pyright: ignore[reportMissingImports]
        dispatch,
    )

    tenant_id = _tenant_of(user_id)
    if tenant_id is None:
        return
    try:
        dispatch(notification_type, tenant_id, context, target_user_ids=[user_id])
    except Exception:
        log.exception("Failed to dispatch %s notification", notification_type)


def _change_details(changes: list[tuple[str, str, str]]) -> list[tuple[Any, str]]:
    return [
        (FIELD_SPECS[f][1], f"{old or '—'} → {new or '—'}") for f, old, new in changes
    ]


def notify_shared_changes(fe: Flight, actor_id: int, before: dict[str, str]) -> None:
    """E-mail every other linked pilot which shared fields *actor_id* changed."""
    after = shared_snapshot(fe)
    changes = [(f, before[f], after[f]) for f in after if before.get(f) != after[f]]
    if not changes:
        return
    actor = _display_name(actor_id)
    for user_id in sorted(fe.other_linked_user_ids(actor_id)):
        dispatch_to_user(
            user_id,
            NotificationType.SHARED_FLIGHT_CHANGED,
            {
                "subject_key": _l("Your flight on %(date)s was updated"),
                "subject_args": {"date": fe.date.isoformat()},
                "notification_title_key": _l("%(name)s updated a flight you're on"),
                "notification_title_args": {"name": actor},
                "notification_message_key": _l(
                    "These details of your flight %(route)s on %(date)s changed:"
                ),
                "notification_message_args": {
                    "route": _route(fe),
                    "date": fe.date.isoformat(),
                },
                "details": _change_details(changes),
                "cta_url": url_for("pilots.view_entry", entry_id=fe.id, _external=True),
            },
        )


def notify_suggestion_created(suggestion: FlightCorrectionSuggestion) -> None:
    fe = _suggestion_flight(suggestion)
    suggester = _display_name(suggestion.suggested_by_user_id)
    changes = [(f, old, new) for f, (old, new) in suggestion.changes.items()]
    for user_id in sorted(shared_editor_ids(fe) - {suggestion.suggested_by_user_id}):
        dispatch_to_user(
            user_id,
            NotificationType.FLIGHT_CORRECTION,
            {
                "subject_key": _l("Correction suggested for your flight on %(date)s"),
                "subject_args": {"date": fe.date.isoformat()},
                "notification_title_key": _l("%(name)s suggests a correction"),
                "notification_title_args": {"name": suggester},
                "notification_message_key": _l(
                    "%(name)s suggests changing your flight %(route)s on %(date)s. "
                    "Accept to apply it for both of you, or reject it."
                ),
                "notification_message_args": {
                    "name": suggester,
                    "route": _route(fe),
                    "date": fe.date.isoformat(),
                },
                "details": _change_details(changes),
                "cta_url": url_for("pilots.logbook", _external=True)
                + "#flight-corrections",
                "cta_label": _l("Review in OpenHangar"),
            },
        )


def notify_suggestion_rejected(
    suggestion: FlightCorrectionSuggestion, actor_id: int
) -> None:
    fe = _suggestion_flight(suggestion)
    actor = _display_name(actor_id)
    changes = [(f, old, new) for f, (old, new) in suggestion.changes.items()]
    dispatch_to_user(
        suggestion.suggested_by_user_id,
        NotificationType.FLIGHT_CORRECTION,
        {
            "subject_key": _l("Correction not applied to your flight on %(date)s"),
            "subject_args": {"date": fe.date.isoformat()},
            "notification_title_key": _l("%(name)s rejected your correction"),
            "notification_title_args": {"name": actor},
            "notification_message_key": _l(
                "Your suggested correction for the flight %(route)s on %(date)s "
                "was not applied."
            ),
            "notification_message_args": {
                "route": _route(fe),
                "date": fe.date.isoformat(),
            },
            "details": _change_details(changes),
            "cta_url": url_for("flights.crew_entry", flight_id=fe.id, _external=True),
        },
    )
