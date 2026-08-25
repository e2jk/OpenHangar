"""Shared validation for MaintenanceTrigger and MaintenanceRecord fields.

``parse_trigger_fields``/``parse_service_fields`` extract the validation
previously inlined directly in ``_save_trigger``/``service_trigger`` in
maintenance/routes.py, following the same pattern as
flights/form_parsing.py and pilots/form_parsing.py: standalone,
importable functions that never raise on arbitrary form data.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date as _date
from typing import Any

from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]
from models import HoursBasis, TriggerType  # pyright: ignore[reportMissingImports]


def _parse_iso_date(raw: str) -> _date | None:
    try:
        return _date.fromisoformat(raw)
    except ValueError:
        return None


def _parse_positive_int(raw: str) -> int | None:
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n > 0 else None


def _parse_nonneg_float(raw: str) -> float | None:
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if math.isfinite(v) and v >= 0 else None


def _parse_nonneg_int(raw: str) -> int | None:
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n >= 0 else None


def _parse_positive_float(raw: str) -> float | None:
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if math.isfinite(v) and v > 0 else None


def _parse_optional_float(raw: str) -> float | None:
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def parse_trigger_fields(f: Mapping[str, str]) -> tuple[dict[str, Any], list[str]]:
    """Parse + validate the editable MaintenanceTrigger fields.

    Mirrors ``_save_trigger``'s pre-existing logic exactly.
    """
    errors: list[str] = []

    name = (f.get("name") or "").strip()
    trigger_type = (f.get("trigger_type") or "").strip()
    component_id_raw = (f.get("component_id") or "").strip()
    due_date_raw = (f.get("due_date") or "").strip()
    interval_days_raw = (f.get("interval_days") or "").strip()
    warn_days_raw = (f.get("warn_days") or "").strip()
    due_engine_hours_raw = (f.get("due_engine_hours") or "").strip()
    interval_hours_raw = (f.get("interval_hours") or "").strip()
    warn_hours_raw = (f.get("warn_hours") or "").strip()
    hours_basis_raw = (f.get("hours_basis") or "").strip()
    due_landings_raw = (f.get("due_landings") or "").strip()
    interval_landings_raw = (f.get("interval_landings") or "").strip()
    warn_landings_raw = (f.get("warn_landings") or "").strip()
    notes = (f.get("notes") or "").strip() or None

    if not name:
        errors.append(_("Name is required."))
    if trigger_type not in TriggerType.ALL:
        errors.append(_("Trigger type must be 'calendar', 'hours', or 'landings'."))

    # Ownership (does this ID actually belong to the aircraft?) is checked by
    # the caller, which has the aircraft in scope — this parser only knows
    # whether the value looks like an ID at all.
    component_id: int | None = None
    if component_id_raw:
        component_id = _parse_positive_int(component_id_raw)
        if component_id is None:
            errors.append(_("Component selection is invalid."))

    due_date = interval_days = warn_days = due_engine_hours = interval_hours = None
    warn_hours = due_landings = interval_landings = warn_landings = None
    hours_basis = (
        hours_basis_raw if hours_basis_raw in HoursBasis.ALL else HoursBasis.ENGINE
    )

    if trigger_type == TriggerType.CALENDAR:
        if not due_date_raw:
            errors.append(_("Due date is required for calendar triggers."))
        else:
            due_date = _parse_iso_date(due_date_raw)
            if due_date is None:
                errors.append(_("Due date must be a valid date (YYYY-MM-DD)."))
        if interval_days_raw:
            interval_days = _parse_positive_int(interval_days_raw)
            if interval_days is None:
                errors.append(_("Interval (days) must be a positive integer."))
        if warn_days_raw:
            warn_days = _parse_nonneg_int(warn_days_raw)
            if warn_days is None:
                errors.append(_("Warning lead time (days) must be a positive number."))

    elif trigger_type == TriggerType.HOURS:
        if not due_engine_hours_raw:
            errors.append(_("Due engine hours is required for hours triggers."))
        else:
            due_engine_hours = _parse_nonneg_float(due_engine_hours_raw)
            if due_engine_hours is None:
                errors.append(_("Due engine hours must be a positive number."))
        if interval_hours_raw:
            interval_hours = _parse_positive_float(interval_hours_raw)
            if interval_hours is None:
                errors.append(_("Interval (hours) must be a positive number."))
        if warn_hours_raw:
            warn_hours = _parse_nonneg_float(warn_hours_raw)
            if warn_hours is None:
                errors.append(_("Warning lead time (hours) must be a positive number."))

    elif trigger_type == TriggerType.LANDINGS:
        if not due_landings_raw:
            errors.append(_("Due landings is required for landings triggers."))
        else:
            due_landings = _parse_nonneg_int(due_landings_raw)
            if due_landings is None:
                errors.append(_("Due landings must be a positive number."))
        if interval_landings_raw:
            interval_landings = _parse_positive_int(interval_landings_raw)
            if interval_landings is None:
                errors.append(_("Interval (landings) must be a positive number."))
        if warn_landings_raw:
            warn_landings = _parse_nonneg_int(warn_landings_raw)
            if warn_landings is None:
                errors.append(
                    _("Warning lead time (landings) must be a positive number.")
                )

    values: dict[str, Any] = {
        "name": name,
        "trigger_type": trigger_type,
        "component_id": component_id,
        "due_date": due_date,
        "interval_days": interval_days,
        "warn_days": warn_days,
        "due_engine_hours": due_engine_hours,
        "interval_hours": interval_hours,
        "warn_hours": warn_hours,
        "hours_basis": hours_basis,
        "due_landings": due_landings,
        "interval_landings": interval_landings,
        "warn_landings": warn_landings,
        "notes": notes,
    }
    return values, errors


def parse_service_fields(
    f: Mapping[str, str], requires_hobbs: bool, requires_landings: bool
) -> tuple[dict[str, Any], list[str]]:
    """Parse + validate the editable MaintenanceRecord (service) fields.

    ``requires_hobbs``/``requires_landings`` reflect which due-field groups
    are actually populated on the trigger being serviced (``due_engine_hours
    is not None`` / ``due_landings is not None``) rather than its
    ``trigger_type`` — a combined-interval trigger (Phase 40) can have more
    than one group populated at once, and each populated group's reading is
    required; an unpopulated group's reading stays optional (parsed
    opportunistically if provided, ignored otherwise).
    """
    errors: list[str] = []

    performed_raw = (f.get("performed_at") or "").strip()
    hobbs_raw = (f.get("hobbs_at_service") or "").strip()
    landings_raw = (f.get("landings_at_service") or "").strip()
    notes = (f.get("notes") or "").strip() or None

    performed_at: _date | None = None
    if not performed_raw:
        errors.append(_("Service date is required."))
    else:
        performed_at = _parse_iso_date(performed_raw)
        if performed_at is None:
            errors.append(_("Service date must be a valid date (YYYY-MM-DD)."))

    hobbs_at_service: float | None = None
    if requires_hobbs:
        if not hobbs_raw:
            errors.append(_("Hobbs at service is required for hours-based triggers."))
        else:
            hobbs_at_service = _parse_nonneg_float(hobbs_raw)
            if hobbs_at_service is None:
                errors.append(_("Hobbs at service must be a positive number."))
    elif hobbs_raw:
        hobbs_at_service = _parse_optional_float(hobbs_raw)

    landings_at_service: int | None = None
    if requires_landings:
        if not landings_raw:
            errors.append(
                _("Landings at service is required for landings-based triggers.")
            )
        else:
            landings_at_service = _parse_nonneg_int(landings_raw)
            if landings_at_service is None:
                errors.append(_("Landings at service must be a positive number."))
    elif landings_raw:
        landings_at_service = _parse_nonneg_int(landings_raw)

    values: dict[str, Any] = {
        "performed_at": performed_at,
        "hobbs_at_service": hobbs_at_service,
        "landings_at_service": landings_at_service,
        "notes": notes,
    }
    return values, errors
