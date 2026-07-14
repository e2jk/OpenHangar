"""Shared validation for the standalone PilotLogbookEntry editable field set.

``parse_pilot_fields`` / ``apply_pilot_fields`` are used by both the online
pilot-logbook form (``new_entry`` / ``edit_entry`` in ``pilots/routes.py``)
and the offline sync API (``offline/routes.py``) so the two paths can never
diverge. The field set matches
``offline.serialize.PILOT_EDITABLE_FIELDS`` exactly — the full,
standalone-entry set. A *linked* entry (tied to a FlightEntry) only ever
exposes ``PILOT_LINKED_EDITABLE_FIELDS``, handled separately by
``apply_linked_pilot_entry`` in ``flights/routes.py``.
"""

from collections.abc import Mapping
from datetime import date as _date, time as _time
from typing import Any

from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

from models import FstdType, LogbookEntryType, PilotLogbookEntry  # pyright: ignore[reportMissingImports]


def _parse_time(val: str, field: str) -> tuple[_time | None, str | None]:
    val = val.strip()
    if not val:
        return None, None
    try:
        h, m = val.split(":")
        t = _time(int(h), int(m))
        return t, None
    except (ValueError, AttributeError):
        return None, _("%(field)s: enter a valid HH:MM time.", field=field)


def _parse_decimal(val: str, field: str) -> tuple[float | None, str | None]:
    val = val.strip()
    if not val:
        return None, None
    try:
        n = float(val)
        if n < 0:
            return None, _("%(field)s: must be non-negative.", field=field)
        return n, None
    except ValueError:
        return None, _("%(field)s: must be a number.", field=field)


def _parse_int(val: str, field: str) -> tuple[int | None, str | None]:
    val = val.strip()
    if not val:
        return None, None
    try:
        n = int(val)
        if n < 0:
            return None, _("%(field)s: must be non-negative.", field=field)
        return n, None
    except ValueError:
        return None, _("%(field)s: must be a whole number.", field=field)


def _parse_date(val: str, field: str) -> tuple[_date | None, str | None]:
    val = val.strip()
    if not val:
        return None, None
    try:
        return _date.fromisoformat(val), None
    except ValueError:
        return None, _("%(field)s: enter a valid date (YYYY-MM-DD).", field=field)


def parse_pilot_fields(f: Mapping[str, str]) -> tuple[dict[str, Any], list[str]]:
    """Parse + validate the editable standalone PilotLogbookEntry fields.

    Mirrors ``_entry_from_form``'s existing logic exactly: date required/ISO;
    times ``HH:MM``; decimals/ints non-negative; the FSTD toggle nulls
    flight-only fields when ``entry_type == "fstd"`` and nulls
    ``fstd_type``/``fstd_duration`` otherwise.
    """
    errors: list[str] = []

    entry_type = (f.get("entry_type") or "").strip() or LogbookEntryType.FLIGHT
    if entry_type not in LogbookEntryType.ALL:
        entry_type = LogbookEntryType.FLIGHT
    is_fstd = entry_type == LogbookEntryType.FSTD

    fstd_type = (f.get("fstd_type") or "").strip() or None
    if fstd_type not in FstdType.ALL:
        fstd_type = None
    fstd_duration, err = _parse_decimal(f.get("fstd_duration", ""), "Sim duration")
    if err:
        errors.append(err)

    date_val, err = _parse_date(f.get("date", ""), "Date")
    if err:
        errors.append(err)
    elif date_val is None:
        from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

        errors.append(_("Date is required."))

    dep_time, err = _parse_time(f.get("departure_time", ""), "Departure time")
    if err:
        errors.append(err)
    arr_time, err = _parse_time(f.get("arrival_time", ""), "Arrival time")
    if err:
        errors.append(err)

    night_time, err = _parse_decimal(f.get("night_time", ""), "Night time")
    if err:
        errors.append(err)
    instrument_time, err = _parse_decimal(
        f.get("instrument_time", ""), "Instrument time"
    )
    if err:
        errors.append(err)
    landings_day, err = _parse_int(f.get("landings_day", ""), "Day landings")
    if err:
        errors.append(err)
    landings_night, err = _parse_int(f.get("landings_night", ""), "Night landings")
    if err:
        errors.append(err)
    sp_se, err = _parse_decimal(f.get("single_pilot_se", ""), "S/E time")
    if err:
        errors.append(err)
    sp_me, err = _parse_decimal(f.get("single_pilot_me", ""), "M/E time")
    if err:
        errors.append(err)
    multi_pilot, err = _parse_decimal(f.get("multi_pilot", ""), "Multi-pilot time")
    if err:
        errors.append(err)
    fn_pic, err = _parse_decimal(f.get("function_pic", ""), "PIC function")
    if err:
        errors.append(err)
    fn_co, err = _parse_decimal(f.get("function_copilot", ""), "Co-pilot function")
    if err:
        errors.append(err)
    fn_dual, err = _parse_decimal(f.get("function_dual", ""), "Dual function")
    if err:
        errors.append(err)
    fn_inst, err = _parse_decimal(
        f.get("function_instructor", ""), "Instructor function"
    )
    if err:
        errors.append(err)

    values: dict[str, Any] = {
        "date": date_val,
        "aircraft_type": None
        if is_fstd
        else (f.get("aircraft_type") or "").strip() or None,
        "aircraft_type_icao": None
        if is_fstd
        else (f.get("aircraft_type_icao") or "").strip() or None,
        "aircraft_registration": None
        if is_fstd
        else (f.get("aircraft_registration") or "").strip() or None,
        "departure_place": None
        if is_fstd
        else (f.get("departure_place") or "").strip() or None,
        "departure_time": None if is_fstd else dep_time,
        "arrival_place": None
        if is_fstd
        else (f.get("arrival_place") or "").strip() or None,
        "arrival_time": None if is_fstd else arr_time,
        "pic_name": (f.get("pic_name") or "").strip() or None,
        "night_time": night_time,
        "instrument_time": instrument_time,
        "landings_day": None if is_fstd else landings_day,
        "landings_night": None if is_fstd else landings_night,
        "single_pilot_se": None if is_fstd else sp_se,
        "single_pilot_me": None if is_fstd else sp_me,
        "multi_pilot": None if is_fstd else multi_pilot,
        "function_pic": fn_pic,
        "function_copilot": fn_co,
        "function_dual": fn_dual,
        "function_instructor": fn_inst,
        "remarks": (f.get("remarks") or "").strip() or None,
        "entry_type": entry_type,
        "fstd_type": fstd_type if is_fstd else None,
        "fstd_duration": fstd_duration if is_fstd else None,
    }
    return values, errors


def parse_linked_pilot_fields(f: Mapping[str, str]) -> tuple[dict[str, Any], list[str]]:
    """Parse + validate the user-entered subset of a *linked* PilotLogbookEntry.

    Field set matches ``offline.serialize.PILOT_LINKED_EDITABLE_FIELDS``. An
    empty ``departure_time``/``arrival_time`` parses to ``None``, meaning
    "mirror the flight's corresponding time" — resolved by the caller
    (``flights.routes.apply_linked_pilot_entry``) against the current flight.
    """
    errors: list[str] = []
    night_time, err = _parse_decimal(f.get("night_time", ""), "Night time")
    if err:
        errors.append(err)
    instrument_time, err = _parse_decimal(
        f.get("instrument_time", ""), "Instrument time"
    )
    if err:
        errors.append(err)
    landings_day, err = _parse_int(f.get("landings_day", ""), "Day landings")
    if err:
        errors.append(err)
    landings_night, err = _parse_int(f.get("landings_night", ""), "Night landings")
    if err:
        errors.append(err)
    multi_pilot, err = _parse_decimal(f.get("multi_pilot", ""), "Multi-pilot time")
    if err:
        errors.append(err)
    dep_time, err = _parse_time(f.get("departure_time", ""), "Departure time")
    if err:
        errors.append(err)
    arr_time, err = _parse_time(f.get("arrival_time", ""), "Arrival time")
    if err:
        errors.append(err)

    values: dict[str, Any] = {
        "night_time": night_time,
        "instrument_time": instrument_time,
        "landings_day": landings_day,
        "landings_night": landings_night,
        "multi_pilot": multi_pilot,
        "pic_name": (f.get("pic_name") or "").strip() or None,
        "departure_time": dep_time,
        "arrival_time": arr_time,
    }
    return values, errors


def apply_pilot_fields(entry: PilotLogbookEntry, values: dict[str, Any]) -> None:
    """Assign parsed editable-field values onto ``entry``.

    Mirrors ``edit_entry``'s pre-existing behaviour exactly: ``cross_country``
    has no form field anywhere, so it is always nulled on a standalone save
    (this matches copying *all* table columns from a freshly-built entry,
    which is what the online form did before this extraction).
    """
    for key, value in values.items():
        setattr(entry, key, value)
    entry.cross_country = None
