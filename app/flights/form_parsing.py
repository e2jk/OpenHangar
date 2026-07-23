"""Shared validation for the FlightEntry editable field set.

``parse_flight_fields`` / ``apply_flight_fields`` are used by both the
online flight form (``_handle_log_flight_post``) and the offline sync API
(``offline/routes.py``) so the two paths can never diverge in validation.
The field set matches ``offline.serialize.FLIGHT_EDITABLE_FIELDS`` exactly.
"""

from collections.abc import Mapping
from datetime import date as _date, time as _time
from typing import Any

from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

from models import Aircraft, CrewRole, FlightCrew, FlightEntry, db  # pyright: ignore[reportMissingImports]


def parse_flight_fields(
    f: Mapping[str, str], ac: Aircraft | None
) -> tuple[dict[str, Any], list[str]]:
    """Parse + validate the editable FlightEntry fields from raw strings.

    ``ac`` gates the aircraft-log-specific rules (counters, flight-time
    derivation from counters, crew-1 required) exactly like the ``if ac:``
    branches in the online form — pass ``None`` for flights with no
    fleet aircraft (the "other aircraft" case), matching today's behaviour.
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
    if ac and not crew_name_0:
        errors.append(_("Pilot (crew 1) name is required."))

    departure_time_raw = (f.get("departure_time") or "").strip()
    arrival_time_raw = (f.get("arrival_time") or "").strip()
    departure_time: _time | None = None
    arrival_time: _time | None = None
    if departure_time_raw:
        try:
            departure_time = _time.fromisoformat(departure_time_raw)
        except ValueError:
            errors.append(_("Departure time must be a valid UTC time (HH:MM)."))
    if arrival_time_raw:
        try:
            arrival_time = _time.fromisoformat(arrival_time_raw)
        except ValueError:
            errors.append(_("Arrival time must be a valid UTC time (HH:MM)."))

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
                    if val < 0:
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

    flight_time_raw = (f.get("flight_time") or "").strip()
    flight_time: float | None = None
    if flight_time_raw:
        try:
            flight_time = round(float(flight_time_raw), 1)
            if flight_time < 0:
                raise ValueError
        except (ValueError, TypeError):
            flight_time = None
            errors.append(_("Flight time must be a non-negative number."))
    elif (
        ac
        and flight_time_counter_start is not None
        and flight_time_counter_end is not None
    ):
        # Clamped like the engine-counter branch below: an end-before-start
        # counter pair already appends an error above, but flight_time is
        # still returned to the caller regardless of errors, so it must
        # never come back negative.
        flight_time = round(
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
        flight_time = round(max(0.0, raw_diff), 1)

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

    fuel_event_raw = (f.get("fuel_event") or "none").strip()
    fuel_event = fuel_event_raw if fuel_event_raw in ("before", "after") else None
    fuel_added_qty_raw = (f.get("fuel_added_qty") or "").strip()
    fuel_added_qty: float | None = None
    if fuel_event and fuel_added_qty_raw:
        try:
            fuel_added_qty = float(fuel_added_qty_raw)
            if fuel_added_qty < 0:
                raise ValueError
        except (ValueError, TypeError):
            fuel_added_qty = None
            errors.append(_("Fuel quantity added must be a non-negative number."))
    fuel_added_unit = (f.get("fuel_added_unit") or "L").strip()

    fuel_remaining_qty_raw = (f.get("fuel_remaining_qty") or "").strip()
    fuel_remaining_qty: float | None = None
    if fuel_remaining_qty_raw:
        try:
            fuel_remaining_qty = float(fuel_remaining_qty_raw)
            if fuel_remaining_qty < 0:
                raise ValueError
        except (ValueError, TypeError):
            fuel_remaining_qty = None
            errors.append(_("Fuel remaining must be a non-negative number."))

    oil_added_l_raw = (f.get("oil_added_l") or "").strip()
    oil_added_l: float | None = None
    if oil_added_l_raw:
        try:
            oil_added_l = float(oil_added_l_raw)
            if oil_added_l < 0:
                raise ValueError
        except (ValueError, TypeError):
            oil_added_l = None
            errors.append(_("Oil added must be a non-negative number."))

    nature_of_flight = (f.get("nature_of_flight") or "").strip() or None
    notes = (f.get("notes") or "").strip() or None

    values: dict[str, Any] = {
        "date": flight_date,
        "departure_icao": dep,
        "arrival_icao": arr,
        "departure_time": departure_time,
        "arrival_time": arrival_time,
        "flight_time": flight_time,
        "flight_time_counter_start": flight_time_counter_start,
        "flight_time_counter_end": flight_time_counter_end,
        "engine_time_counter_start": engine_time_counter_start,
        "engine_time_counter_end": engine_time_counter_end,
        "fuel_added_qty": fuel_added_qty,
        "fuel_added_unit": fuel_added_unit if fuel_added_qty is not None else None,
        "fuel_remaining_qty": fuel_remaining_qty,
        "oil_added_l": oil_added_l,
        "passenger_count": passenger_count,
        "landing_count": landing_count,
        "nature_of_flight": nature_of_flight,
        "notes": notes,
        "fuel_event": fuel_event,
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


def apply_flight_fields(fe: FlightEntry, values: dict[str, Any]) -> None:
    """Assign parsed editable-field values onto ``fe`` and replace its crew.

    Mirrors ``_handle_log_flight_post``'s aircraft-log assignment exactly:
    scalar fields are always overwritten; a crew slot is (re)created only
    when its name is non-empty. Flushes to obtain ``fe.id`` for brand-new
    entries before writing the crew rows.
    """
    fe.date = values["date"]
    fe.departure_icao = values["departure_icao"]
    fe.arrival_icao = values["arrival_icao"]
    fe.departure_time = values["departure_time"]
    fe.arrival_time = values["arrival_time"]
    fe.flight_time = values["flight_time"]
    fe.nature_of_flight = values["nature_of_flight"]
    fe.passenger_count = values["passenger_count"]
    fe.landing_count = values["landing_count"]
    fe.flight_time_counter_start = values["flight_time_counter_start"]
    fe.flight_time_counter_end = values["flight_time_counter_end"]
    fe.notes = values["notes"]
    fe.engine_time_counter_start = values["engine_time_counter_start"]
    fe.engine_time_counter_end = values["engine_time_counter_end"]
    fe.fuel_event = values["fuel_event"]
    fe.fuel_added_qty = values["fuel_added_qty"]
    fe.fuel_added_unit = values["fuel_added_unit"]
    fe.fuel_remaining_qty = values["fuel_remaining_qty"]
    fe.oil_added_l = values["oil_added_l"]

    db.session.flush()

    FlightCrew.query.filter_by(flight_id=fe.id).delete()
    if values["crew_name_0"]:
        db.session.add(
            FlightCrew(
                flight_id=fe.id,
                name=values["crew_name_0"],
                role=values["crew_role_0"],
                sort_order=0,
            )
        )
    if values["crew_name_1"]:
        db.session.add(
            FlightCrew(
                flight_id=fe.id,
                name=values["crew_name_1"],
                role=values["crew_role_1"],
                sort_order=1,
            )
        )
