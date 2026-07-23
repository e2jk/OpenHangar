"""Shared validation for MaintenanceTrigger and MaintenanceRecord fields.

``parse_trigger_fields``/``parse_service_fields`` extract the validation
previously inlined directly in ``_save_trigger``/``service_trigger`` in
maintenance/routes.py, following the same pattern as
flights/form_parsing.py and pilots/form_parsing.py: standalone,
importable functions that never raise on arbitrary form data.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date as _date
from typing import Any

from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

from models import TriggerType  # pyright: ignore[reportMissingImports]


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
    return v if v >= 0 else None


def _parse_positive_float(raw: str) -> float | None:
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if v > 0 else None


def _parse_optional_float(raw: str) -> float | None:
    try:
        return float(raw)
    except ValueError:
        return None


def parse_trigger_fields(f: Mapping[str, str]) -> tuple[dict[str, Any], list[str]]:
    """Parse + validate the editable MaintenanceTrigger fields.

    Mirrors ``_save_trigger``'s pre-existing logic exactly.
    """
    errors: list[str] = []

    name = (f.get("name") or "").strip()
    trigger_type = (f.get("trigger_type") or "").strip()
    due_date_raw = (f.get("due_date") or "").strip()
    interval_days_raw = (f.get("interval_days") or "").strip()
    due_engine_hours_raw = (f.get("due_engine_hours") or "").strip()
    interval_hours_raw = (f.get("interval_hours") or "").strip()
    notes = (f.get("notes") or "").strip() or None

    if not name:
        errors.append(_("Name is required."))
    if trigger_type not in TriggerType.ALL:
        errors.append(_("Trigger type must be 'calendar' or 'hours'."))

    due_date = interval_days = due_engine_hours = interval_hours = None

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

    values: dict[str, Any] = {
        "name": name,
        "trigger_type": trigger_type,
        "due_date": due_date,
        "interval_days": interval_days,
        "due_engine_hours": due_engine_hours,
        "interval_hours": interval_hours,
        "notes": notes,
    }
    return values, errors


def parse_service_fields(
    f: Mapping[str, str], trigger_type: str
) -> tuple[dict[str, Any], list[str]]:
    """Parse + validate the editable MaintenanceRecord (service) fields.

    Mirrors ``service_trigger``'s pre-existing logic exactly.
    """
    errors: list[str] = []

    performed_raw = (f.get("performed_at") or "").strip()
    hobbs_raw = (f.get("hobbs_at_service") or "").strip()
    notes = (f.get("notes") or "").strip() or None

    performed_at: _date | None = None
    if not performed_raw:
        errors.append(_("Service date is required."))
    else:
        performed_at = _parse_iso_date(performed_raw)
        if performed_at is None:
            errors.append(_("Service date must be a valid date (YYYY-MM-DD)."))

    hobbs_at_service: float | None = None
    if trigger_type == TriggerType.HOURS:
        if not hobbs_raw:
            errors.append(_("Hobbs at service is required for hours-based triggers."))
        else:
            hobbs_at_service = _parse_nonneg_float(hobbs_raw)
            if hobbs_at_service is None:
                errors.append(_("Hobbs at service must be a positive number."))
    elif hobbs_raw:
        hobbs_at_service = _parse_optional_float(hobbs_raw)

    values: dict[str, Any] = {
        "performed_at": performed_at,
        "hobbs_at_service": hobbs_at_service,
        "notes": notes,
    }
    return values, errors
