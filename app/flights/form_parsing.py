"""Shared validation for the unified Flight editable field set (airframe side).

``parse_flight_fields`` / ``apply_flight_fields`` are used by both the
online flight form (``_handle_log_flight_post``) and the offline sync API
(``offline/routes.py``) so the two paths can never diverge in validation.
The field set matches ``offline.serialize.FLIGHT_EDITABLE_FIELDS`` exactly.
"""

import math
from collections.abc import Mapping
from datetime import (
    date as _date,
)
from datetime import (
    datetime as _datetime,
)
from datetime import (
    time as _time,
)
from datetime import (
    timedelta as _timedelta,
)
from typing import Any

from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    CrewRole,
    Flight,
    db,
)

# engine_time/flight_time are never taken as free-text user input — always
# recomputed from counters (preferred) or clock times (departure/arrival for
# engine, takeoff/landing for flight), matching whichever data the pilot
# actually entered. When both a counter pair and a clock-time pair are
# present for the same category, they're two independent measurements of
# the same thing and must agree within this tolerance — a bigger gap means
# a data-entry mistake (wrong counter digit, wrong time), not rounding.
_DURATION_MISMATCH_TOLERANCE_HOURS = 0.2


def _parse_clock_time(raw: str) -> _time:
    """Parse an HH:MM(:SS) UTC clock time.

    ``time.fromisoformat`` also accepts a UTC-offset suffix (e.g.
    ``"04:23+02:00"``), which would produce a timezone-aware ``time`` that
    can't be compared against the naive ones from plain ``"HH:MM"`` input in
    ``_hours_between``. These fields are always UTC — reject offsets rather
    than silently accept them.
    """
    parsed = _time.fromisoformat(raw)
    if parsed.tzinfo is not None:
        raise ValueError("UTC clock time must not carry a timezone offset")
    return parsed


def _hours_between(start: _time, end: _time) -> float:
    """Duration in hours between two clock times, assuming ``end`` is on the
    same day as ``start`` unless it's earlier (then the flight crossed
    midnight)."""
    start_dt = _datetime.combine(_date.min, start)
    end_dt = _datetime.combine(_date.min, end)
    if end_dt < start_dt:
        end_dt += _timedelta(days=1)
    return (end_dt - start_dt).total_seconds() / 3600.0


def flight_is_lenient(fe: Flight | None) -> bool:
    """True when *fe* came from a bulk airframe import batch that's been
    flagged as historical (digitizing pre-existing paper records) — such a
    batch's rows may carry OCR/paper-log imprecision that was never wrong,
    just imprecise, and must not block later edits. ``fe`` is ``None`` when
    creating a new flight, which is always strict."""
    return bool(
        fe and fe.airframe_import_batch and fe.airframe_import_batch.is_historical
    )


def parse_flight_fields(
    f: Mapping[str, str], ac: Aircraft | None, strict: bool = True
) -> tuple[dict[str, Any], list[str]]:
    """Parse + validate the editable FlightEntry fields from raw strings.

    ``ac`` gates the aircraft-log-specific rules (counters, flight-time
    derivation from counters, crew-1 required) exactly like the ``if ac:``
    branches in the online form — pass ``None`` for flights with no
    fleet aircraft (the "other aircraft" case), matching today's behaviour.

    ``strict=False`` (see ``flight_is_lenient``) suppresses only the two
    checks that can block saving an edit to data that was never freshly
    typed — pilot-name-required and counter/clock duration mismatches.
    Everything else (date required, parse failures, counter-end-before-
    start) still applies regardless: those are new mistakes made today, not
    pre-existing historical imprecision.
    """
    errors: list[str] = []

    date_raw = (f.get("date") or "").strip()
    flight_date: _date | None = None
    if not date_raw:
        errors.append(_("Date is required."))
    else:
        try:
            flight_date = _date.fromisoformat(date_raw)
        except ValueError:
            errors.append(_("Date must be a valid date (YYYY-MM-DD)."))

    dep = (f.get("departure_icao") or "").strip().upper()[:4]
    arr = (f.get("arrival_icao") or "").strip().upper()[:4]
    if not dep:
        errors.append(_("Departure airfield is required."))
    if not arr:
        errors.append(_("Arrival airfield is required."))

    crew_name_0 = (f.get("crew_name_0") or "").strip()
    crew_role_0_raw = (f.get("crew_role_0") or CrewRole.PIC).strip()
    crew_name_1 = (f.get("crew_name_1") or "").strip()
    crew_role_1_raw = (f.get("crew_role_1") or CrewRole.COPILOT).strip()
    if ac and not crew_name_0 and strict:
        errors.append(_("Pilot (crew 1) name is required."))

    departure_time_raw = (f.get("departure_time") or "").strip()
    arrival_time_raw = (f.get("arrival_time") or "").strip()
    departure_time: _time | None = None
    arrival_time: _time | None = None
    if departure_time_raw:
        try:
            departure_time = _parse_clock_time(departure_time_raw)
        except ValueError:
            errors.append(_("Departure time must be a valid UTC time (HH:MM)."))
    if arrival_time_raw:
        try:
            arrival_time = _parse_clock_time(arrival_time_raw)
        except ValueError:
            errors.append(_("Arrival time must be a valid UTC time (HH:MM)."))

    # Actual airborne segment — optional, independent of the block times
    # above, never defaulted from them.
    takeoff_time_raw = (f.get("takeoff_time") or "").strip()
    landing_time_raw = (f.get("landing_time") or "").strip()
    takeoff_time: _time | None = None
    landing_time: _time | None = None
    if takeoff_time_raw:
        try:
            takeoff_time = _parse_clock_time(takeoff_time_raw)
        except ValueError:
            errors.append(_("Takeoff time must be a valid UTC time (HH:MM)."))
    if landing_time_raw:
        try:
            landing_time = _parse_clock_time(landing_time_raw)
        except ValueError:
            errors.append(_("Landing time must be a valid UTC time (HH:MM)."))

    flight_time_counter_start = flight_time_counter_end = None
    engine_time_counter_start = engine_time_counter_end = None
    if ac:
        for raw, dest in [
            ((f.get("flight_time_counter_start") or "").strip(), "fc_start"),
            ((f.get("flight_time_counter_end") or "").strip(), "fc_end"),
            ((f.get("engine_time_counter_start") or "").strip(), "ec_start"),
            ((f.get("engine_time_counter_end") or "").strip(), "ec_end"),
        ]:
            if raw:
                try:
                    val = float(raw)
                    if not math.isfinite(val) or val < 0:
                        raise ValueError
                    if dest == "fc_start":
                        flight_time_counter_start = val
                    elif dest == "fc_end":
                        flight_time_counter_end = val
                    elif dest == "ec_start":
                        engine_time_counter_start = val
                    else:
                        engine_time_counter_end = val
                except (ValueError, TypeError):
                    errors.append(_("Counter value must be a positive number."))

        if (
            flight_time_counter_start is not None
            and flight_time_counter_end is not None
            and flight_time_counter_end < flight_time_counter_start
        ):
            errors.append(
                _("Flight counter end must not be less than flight counter start.")
            )
        if (
            engine_time_counter_start is not None
            and engine_time_counter_end is not None
            and engine_time_counter_end < engine_time_counter_start
        ):
            errors.append(
                _("Engine counter end must not be less than engine counter start.")
            )

    # engine_time: never user-entered — computed from the engine counters
    # and/or the engine start/end clock times (departure_time/arrival_time),
    # whichever are present. If both are present they must roughly agree.
    engine_time_from_counters: float | None = None
    if engine_time_counter_start is not None and engine_time_counter_end is not None:
        engine_time_from_counters = round(
            max(0.0, engine_time_counter_end - engine_time_counter_start), 1
        )
    engine_time_from_clock: float | None = None
    if departure_time is not None and arrival_time is not None:
        engine_time_from_clock = round(_hours_between(departure_time, arrival_time), 1)

    if (
        strict
        and engine_time_from_counters is not None
        and engine_time_from_clock is not None
        and abs(engine_time_from_counters - engine_time_from_clock)
        > _DURATION_MISMATCH_TOLERANCE_HOURS
    ):
        errors.append(
            _(
                "Engine time from the counters (%(counters)s h) doesn't match the "
                "departure/arrival times (%(clock)s h) — check for a data entry "
                "mistake.",
                counters=f"{engine_time_from_counters:.1f}",
                clock=f"{engine_time_from_clock:.1f}",
            )
        )
    engine_time = (
        engine_time_from_counters
        if engine_time_from_counters is not None
        else engine_time_from_clock
    )

    # flight_time: same principle — computed from the flight counters (or,
    # for aircraft with no separate flight-hour meter, the engine counters
    # minus the configured offset) and/or the takeoff/landing clock times.
    flight_time_from_counters: float | None = None
    if (
        ac
        and flight_time_counter_start is not None
        and flight_time_counter_end is not None
    ):
        # Clamped: an end-before-start counter pair already appends an error
        # above, but flight_time is still returned to the caller regardless
        # of errors, so it must never come back negative.
        flight_time_from_counters = round(
            max(0.0, flight_time_counter_end - flight_time_counter_start), 1
        )
    elif (
        ac
        and not getattr(ac, "has_flight_counter", True)
        and engine_time_counter_start is not None
        and engine_time_counter_end is not None
    ):
        raw_diff = (engine_time_counter_end - engine_time_counter_start) - float(
            getattr(ac, "flight_counter_offset", 0) or 0
        )
        flight_time_from_counters = round(max(0.0, raw_diff), 1)

    flight_time_from_clock: float | None = None
    if takeoff_time is not None and landing_time is not None:
        flight_time_from_clock = round(_hours_between(takeoff_time, landing_time), 1)

    if (
        strict
        and flight_time_from_counters is not None
        and flight_time_from_clock is not None
        and abs(flight_time_from_counters - flight_time_from_clock)
        > _DURATION_MISMATCH_TOLERANCE_HOURS
    ):
        errors.append(
            _(
                "Flight time from the counters (%(counters)s h) doesn't match the "
                "takeoff/landing times (%(clock)s h) — check for a data entry "
                "mistake.",
                counters=f"{flight_time_from_counters:.1f}",
                clock=f"{flight_time_from_clock:.1f}",
            )
        )
    flight_time = (
        flight_time_from_counters
        if flight_time_from_counters is not None
        else flight_time_from_clock
    )

    passenger_count_raw = (f.get("passenger_count") or "").strip()
    passenger_count: int | None = None
    if passenger_count_raw:
        try:
            passenger_count = int(passenger_count_raw)
            if passenger_count < 0:
                raise ValueError
        except (ValueError, TypeError):
            passenger_count = None
            errors.append(_("Passenger count must be a non-negative integer."))

    landing_count_raw = (f.get("landing_count") or "").strip()
    landing_count: int | None = None
    if landing_count_raw:
        try:
            landing_count = int(landing_count_raw)
            if landing_count < 0:
                raise ValueError
        except (ValueError, TypeError):
            landing_count = None
            errors.append(_("Landing count must be a non-negative integer."))

    fuel_added_before_qty_raw = (f.get("fuel_added_before_qty") or "").strip()
    fuel_added_before_qty: float | None = None
    if fuel_added_before_qty_raw:
        try:
            fuel_added_before_qty = float(fuel_added_before_qty_raw)
            if not math.isfinite(fuel_added_before_qty) or fuel_added_before_qty < 0:
                raise ValueError
        except (ValueError, TypeError):
            fuel_added_before_qty = None
            errors.append(
                _(
                    "Fuel quantity added before the flight must be a non-negative number."
                )
            )
    fuel_added_before_unit = (f.get("fuel_added_before_unit") or "L").strip()

    fuel_added_after_qty_raw = (f.get("fuel_added_after_qty") or "").strip()
    fuel_added_after_qty: float | None = None
    if fuel_added_after_qty_raw:
        try:
            fuel_added_after_qty = float(fuel_added_after_qty_raw)
            if not math.isfinite(fuel_added_after_qty) or fuel_added_after_qty < 0:
                raise ValueError
        except (ValueError, TypeError):
            fuel_added_after_qty = None
            errors.append(
                _("Fuel quantity added after the flight must be a non-negative number.")
            )
    fuel_added_after_unit = (f.get("fuel_added_after_unit") or "L").strip()

    fuel_remaining_qty_raw = (f.get("fuel_remaining_qty") or "").strip()
    fuel_remaining_qty: float | None = None
    if fuel_remaining_qty_raw:
        try:
            fuel_remaining_qty = float(fuel_remaining_qty_raw)
            if not math.isfinite(fuel_remaining_qty) or fuel_remaining_qty < 0:
                raise ValueError
        except (ValueError, TypeError):
            fuel_remaining_qty = None
            errors.append(_("Fuel remaining must be a non-negative number."))

    oil_added_before_l_raw = (f.get("oil_added_before_l") or "").strip()
    oil_added_before_l: float | None = None
    if oil_added_before_l_raw:
        try:
            oil_added_before_l = float(oil_added_before_l_raw)
            if not math.isfinite(oil_added_before_l) or oil_added_before_l < 0:
                raise ValueError
        except (ValueError, TypeError):
            oil_added_before_l = None
            errors.append(
                _("Oil added before the flight must be a non-negative number.")
            )

    oil_added_after_l_raw = (f.get("oil_added_after_l") or "").strip()
    oil_added_after_l: float | None = None
    if oil_added_after_l_raw:
        try:
            oil_added_after_l = float(oil_added_after_l_raw)
            if not math.isfinite(oil_added_after_l) or oil_added_after_l < 0:
                raise ValueError
        except (ValueError, TypeError):
            oil_added_after_l = None
            errors.append(
                _("Oil added after the flight must be a non-negative number.")
            )

    nature_of_flight = (f.get("nature_of_flight") or "").strip() or None
    notes = (f.get("notes") or "").strip() or None

    values: dict[str, Any] = {
        "date": flight_date,
        "departure_icao": dep,
        "arrival_icao": arr,
        "departure_time": departure_time,
        "arrival_time": arrival_time,
        "takeoff_time": takeoff_time,
        "landing_time": landing_time,
        "flight_time": flight_time,
        "flight_time_counter_start": flight_time_counter_start,
        "flight_time_counter_end": flight_time_counter_end,
        "engine_time": engine_time,
        "engine_time_counter_start": engine_time_counter_start,
        "engine_time_counter_end": engine_time_counter_end,
        "fuel_added_before_qty": fuel_added_before_qty,
        "fuel_added_before_unit": fuel_added_before_unit
        if fuel_added_before_qty is not None
        else None,
        "fuel_added_after_qty": fuel_added_after_qty,
        "fuel_added_after_unit": fuel_added_after_unit
        if fuel_added_after_qty is not None
        else None,
        "fuel_remaining_qty": fuel_remaining_qty,
        "oil_added_before_l": oil_added_before_l,
        "oil_added_after_l": oil_added_after_l,
        "passenger_count": passenger_count,
        "landing_count": landing_count,
        "nature_of_flight": nature_of_flight,
        "notes": notes,
        "crew_name_0": crew_name_0,
        "crew_role_0": crew_role_0_raw
        if crew_role_0_raw in CrewRole.ALL
        else CrewRole.PIC,
        "crew_name_1": crew_name_1,
        "crew_role_1": crew_role_1_raw
        if crew_role_1_raw in CrewRole.ALL
        else CrewRole.COPILOT,
    }
    return values, errors


def apply_flight_fields(fe: Flight, values: dict[str, Any]) -> None:
    """Assign parsed editable-field values onto ``fe``, including the crew
    identity slots.

    Mirrors ``_handle_log_flight_post``'s aircraft-log assignment exactly:
    scalar fields are always overwritten. ``crew_name_0``/``crew_role_0``
    (role fixed to PIC) write ``pic_name``; ``crew_name_1``/``crew_role_1``
    write ``second_crew_name``/``second_crew_role`` — a blank name clears
    the slot. Resolving either slot's ``*_user_id`` (matching the form's
    submitter or another OpenHangar user into a slot) is the caller's job
    in ``flights/routes.py``, not this shared field-parsing layer.
    """
    fe.date = values["date"]
    fe.departure_icao = values["departure_icao"]
    fe.arrival_icao = values["arrival_icao"]
    fe.departure_time = values["departure_time"]
    fe.arrival_time = values["arrival_time"]
    fe.takeoff_time = values["takeoff_time"]
    fe.landing_time = values["landing_time"]
    fe.flight_time = values["flight_time"]
    fe.nature_of_flight = values["nature_of_flight"]
    fe.passenger_count = values["passenger_count"]
    fe.landing_count = values["landing_count"]
    fe.flight_time_counter_start = values["flight_time_counter_start"]
    fe.flight_time_counter_end = values["flight_time_counter_end"]
    fe.notes = values["notes"]
    fe.engine_time = values["engine_time"]
    fe.engine_time_counter_start = values["engine_time_counter_start"]
    fe.engine_time_counter_end = values["engine_time_counter_end"]
    fe.fuel_added_before_qty = values["fuel_added_before_qty"]
    fe.fuel_added_before_unit = values["fuel_added_before_unit"]
    fe.fuel_added_after_qty = values["fuel_added_after_qty"]
    fe.fuel_added_after_unit = values["fuel_added_after_unit"]
    fe.fuel_remaining_qty = values["fuel_remaining_qty"]
    fe.oil_added_before_l = values["oil_added_before_l"]
    fe.oil_added_after_l = values["oil_added_after_l"]

    fe.pic_name = values["crew_name_0"] or None
    if values["crew_name_1"]:
        fe.second_crew_name = values["crew_name_1"]
        fe.second_crew_role = values["crew_role_1"]
    else:
        fe.second_crew_name = None
        fe.second_crew_role = None

    db.session.flush()
