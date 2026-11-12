from datetime import date as _date
from datetime import timedelta
from typing import Any

WINDOW_DAYS = 90
# "Recently flown aircraft" statistics: about six months.
STATS_WINDOW_DAYS = 182
PASSENGER_REQUIRED = 3  # FCL.060(b)(1): 3 take-offs/landings (any) in 90 days
NIGHT_REQUIRED = 1  # FCL.060(b)(2): 1 night landing in 90 days
EXPIRY_WARN_DAYS = 90  # warn when medical/SEP expires within this many days
CURRENCY_WARN_DAYS = 30  # warn when landing currency expires within this many days

STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_EXPIRED = "expired"
STATUS_UNKNOWN = "unknown"


def _rolling_landing_currency(
    entries: Any, landing_fields: str | tuple[str, ...], required: int, today: _date
) -> dict[str, Any]:
    """Compute rolling currency for one or more landing fields summed together."""
    if isinstance(landing_fields, str):
        landing_fields = (landing_fields,)

    def _count(e: Any) -> int:
        return sum(getattr(e, f) or 0 for f in landing_fields)

    window_start = today - timedelta(days=WINDOW_DAYS)
    qualifying = [e for e in entries if e.date >= window_start and _count(e) > 0]
    qualifying.sort(key=lambda e: (e.date, e.id), reverse=True)

    total = sum(_count(e) for e in qualifying)
    shortfall = max(0, required - total)

    if total >= required:
        cum = 0
        anchor_date: _date | None = None
        for e in qualifying:
            cum += _count(e)
            if cum >= required:
                anchor_date = e.date
                break
        assert anchor_date is not None  # nosec B101  # mypy narrowing invariant
        expires_on = anchor_date + timedelta(days=WINDOW_DAYS)
        days_left = (expires_on - today).days
        status = STATUS_WARNING if days_left <= CURRENCY_WARN_DAYS else STATUS_OK
    else:
        expires_on = None
        days_left = None
        status = STATUS_EXPIRED if qualifying else STATUS_UNKNOWN

    return {
        "count": total,
        "required": required,
        "status": status,
        "expires_on": expires_on,
        "days_left": days_left,
        "shortfall": shortfall,
    }


def _expiry_status(
    expiry_date: _date | None, today: _date, warn_days: int
) -> tuple[str, int | None]:
    if expiry_date is None:
        return STATUS_UNKNOWN, None
    days = (expiry_date - today).days
    if days < 0:
        return STATUS_EXPIRED, days
    if days <= warn_days:
        return STATUS_WARNING, days
    return STATUS_OK, days


def _entry_icao(entry: Any) -> str | None:
    """The entry's ICAO type: stored, or resolved from its type text."""
    from utils import (
        resolve_aircraft_type_icao,  # pyright: ignore[reportMissingImports]
    )

    icao: str | None = getattr(entry, "display_aircraft_type_icao", None) or None
    return icao or resolve_aircraft_type_icao(
        getattr(entry, "display_aircraft_type", None)
    )


def _recency_key(icao: str) -> tuple[str, str | None, str | None]:
    """(grouping key, EASA class or None, ICAO type or None) for *icao*:
    a class-rated aircraft groups under its class (FCL.060(b): "same type
    or class"), any other under its own type."""
    from utils import aircraft_class_for_icao  # pyright: ignore[reportMissingImports]

    cls = aircraft_class_for_icao(icao)
    if cls:
        return cls, cls, None
    return f"type:{icao}", None, icao


def _is_simulator(entry: Any) -> bool:
    # A simulator session lands on no aircraft (its landings aren't kept):
    # neither counted nor reported as an unresolved type.
    return getattr(entry, "entry_type", None) == "fstd"


_STATUS_ORDER = {STATUS_OK: 0, STATUS_WARNING: 1, STATUS_EXPIRED: 2, STATUS_UNKNOWN: 3}


def per_class_currency(
    entries: Any,
    today: _date | None = None,
    history: list[tuple[str | None, _date]] | None = None,
) -> dict[str, Any]:
    """Rolling 90-day landing recency per EASA class, FCL.060(b): 3 take-offs
    and landings (passengers) and 1 at night (passengers at night) "in an
    aircraft of the same type or class". Piston aeroplanes count by class
    (SEP/MEP, land/sea — utils.aircraft_class_for_icao), any other aircraft
    by ICAO type.

    *entries* need only cover the last 90 days (more is fine). *history*
    — (ICAO type or None, last flown) per type ever logged (flown_history)
    — adds the classes flown before but not lately, as not current.

    Returns {"by_class": [...], "unresolved_count": n}: one dict per class
    or type — key, class, type, passenger, night, status, last_flown and
    types ({ICAO type: landings in the last 90 days}) — current ones first;
    unresolved_count counts the recent flights whose type isn't known.
    """
    if today is None:
        today = _date.today()
    window_start = today - timedelta(days=WINDOW_DAYS)

    buckets: dict[str, list[Any]] = {}
    meta: dict[str, tuple[str | None, str | None]] = {}
    types: dict[str, dict[str, int]] = {}
    last_flown: dict[str, _date] = {}
    unresolved_count = 0

    def _seen(key: str, cls: str | None, icao: str | None, day: _date) -> None:
        meta[key] = (cls, icao)
        if key not in last_flown or day > last_flown[key]:
            last_flown[key] = day

    for entry in entries:
        if _is_simulator(entry) or entry.date < window_start:
            continue
        icao = _entry_icao(entry)
        if not icao:
            unresolved_count += 1
            continue
        key, cls, typ = _recency_key(icao)
        _seen(key, cls, typ, entry.date)
        buckets.setdefault(key, []).append(entry)
        landings = (entry.landings_day or 0) + (entry.landings_night or 0)
        per_type = types.setdefault(key, {})
        per_type[icao] = per_type.get(icao, 0) + landings

    for icao, day in history or []:
        if icao:
            key, cls, typ = _recency_key(icao)
            _seen(key, cls, typ, day)

    rows: list[dict[str, Any]] = []
    for key, (cls, icao) in meta.items():
        class_entries = buckets.get(key, [])
        pax = _rolling_landing_currency(
            class_entries, ("landings_day", "landings_night"), PASSENGER_REQUIRED, today
        )
        night = _rolling_landing_currency(
            class_entries, "landings_night", NIGHT_REQUIRED, today
        )
        statuses = [pax["status"], night["status"]]
        if STATUS_EXPIRED in statuses:
            status = STATUS_EXPIRED
        elif STATUS_WARNING in statuses:
            status = STATUS_WARNING
        elif STATUS_UNKNOWN in statuses:
            status = STATUS_UNKNOWN
        else:
            status = STATUS_OK
        rows.append(
            {
                "key": key,
                "class": cls,
                "type": icao,
                "passenger": pax,
                "night": night,
                "status": status,
                "last_flown": last_flown[key],
                "types": dict(sorted(types.get(key, {}).items())),
            }
        )
    # Current first — passenger currency, then night — then most recently flown.
    rows.sort(
        key=lambda r: (
            _STATUS_ORDER[r["passenger"]["status"]],
            _STATUS_ORDER[r["night"]["status"]],
            -r["last_flown"].toordinal(),
            r["key"],
        )
    )
    return {"by_class": rows, "unresolved_count": unresolved_count}


def recent_aircraft(entries: Any, today: _date | None = None) -> list[dict[str, Any]]:
    """The aircraft flown in the last STATS_WINDOW_DAYS, most recent first:
    registration, type, ICAO type and class, last flown, number of flights
    then, and landings in the last 90 days. Statistics, not a legal check."""
    if today is None:
        today = _date.today()
    since = today - timedelta(days=STATS_WINDOW_DAYS)
    window_start = today - timedelta(days=WINDOW_DAYS)
    from utils import aircraft_class_for_icao  # pyright: ignore[reportMissingImports]

    by_aircraft: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if _is_simulator(entry) or entry.date < since:
            continue
        registration = getattr(entry, "display_registration", None)
        aircraft_type = getattr(entry, "display_aircraft_type", None)
        key = registration or aircraft_type or ""
        row = by_aircraft.get(key)
        if row is None:
            icao = _entry_icao(entry)
            row = by_aircraft[key] = {
                "registration": registration,
                "aircraft_type": aircraft_type,
                "icao": icao,
                "class": aircraft_class_for_icao(icao),
                "last_flown": entry.date,
                "flights": 0,
                "landings_90": 0,
            }
        row["flights"] += 1
        row["last_flown"] = max(row["last_flown"], entry.date)
        if entry.date >= window_start:
            row["landings_90"] += (entry.landings_day or 0) + (
                entry.landings_night or 0
            )
    return sorted(
        by_aircraft.values(),
        key=lambda r: (-r["last_flown"].toordinal(), r["registration"] or ""),
    )


def recent_flights(user_id: int, today: _date | None = None) -> list[Any]:
    """The pilot's flights (either crew slot) of the last STATS_WINDOW_DAYS:
    all currency_summary needs, however long the logbook."""
    from models import Flight  # pyright: ignore[reportMissingImports]
    from sqlalchemy import or_  # pyright: ignore[reportMissingImports]

    since = (today or _date.today()) - timedelta(days=STATS_WINDOW_DAYS)
    result: list[Any] = Flight.query.filter(
        or_(Flight.pic_user_id == user_id, Flight.second_crew_user_id == user_id),
        Flight.date >= since,
    ).all()
    return result


def flown_history(user_id: int) -> list[tuple[str | None, _date]]:
    """(ICAO type or None, last flown) for each aircraft type the pilot
    ever logged, simulator sessions aside: one grouped query, each distinct
    type resolved once — so the recency doesn't load the whole logbook."""
    from models import (  # pyright: ignore[reportMissingImports]
        Aircraft,
        Flight,
        LogbookEntryType,
        db,
    )
    from sqlalchemy import func, or_  # pyright: ignore[reportMissingImports]
    from utils import (
        resolve_aircraft_type_icao,  # pyright: ignore[reportMissingImports]
    )

    rows = (
        db.session.query(
            Aircraft.make,
            Aircraft.model,
            Flight.other_aircraft_type,
            Flight.other_aircraft_type_icao,
            func.max(Flight.date),
        )
        .outerjoin(Aircraft, Flight.aircraft_id == Aircraft.id)
        .filter(
            or_(Flight.pic_user_id == user_id, Flight.second_crew_user_id == user_id),
            Flight.entry_type != LogbookEntryType.FSTD,
        )
        .group_by(
            Aircraft.make,
            Aircraft.model,
            Flight.other_aircraft_type,
            Flight.other_aircraft_type_icao,
        )
        .all()
    )
    result: list[tuple[str | None, _date]] = []
    for make, model, other_type, other_icao, last in rows:
        # Same as Flight.display_aircraft_type(_icao): a managed aircraft's
        # make and model, else the free-text type.
        if make is not None:
            icao = resolve_aircraft_type_icao(f"{make} {model}".strip())
        else:
            icao = other_icao or resolve_aircraft_type_icao(other_type)
        result.append((icao, last))
    return result


def passenger_currency(entries: Any, today: _date | None = None) -> dict[str, Any]:
    """3 landings (day or night) in rolling 90-day window (EASA FCL.060 passenger carry)."""
    if today is None:
        today = _date.today()
    return _rolling_landing_currency(
        entries, ("landings_day", "landings_night"), PASSENGER_REQUIRED, today
    )


def night_currency(entries: Any, today: _date | None = None) -> dict[str, Any]:
    """1 night landing in rolling 90-day window (EASA FCL.060 night passenger carry)."""
    if today is None:
        today = _date.today()
    return _rolling_landing_currency(entries, "landings_night", NIGHT_REQUIRED, today)


def medical_status(profile: Any, today: _date | None = None) -> dict[str, Any]:
    if today is None:
        today = _date.today()
    # Through a renewal already on file (services.document_lifecycle): no
    # "expiring" status while the next medical certificate is uploaded.
    from services.document_lifecycle import (  # pyright: ignore[reportMissingImports]
        medical_covered_until,
    )

    expiry = medical_covered_until(profile, today) if profile else None
    status, days = _expiry_status(expiry, today, EXPIRY_WARN_DAYS)
    return {"expiry": expiry, "status": status, "days_remaining": days}


def sep_status(profile: Any, today: _date | None = None) -> dict[str, Any]:
    if today is None:
        today = _date.today()
    expiry = profile.sep_expiry if profile else None
    status, days = _expiry_status(expiry, today, EXPIRY_WARN_DAYS)
    return {"expiry": expiry, "status": status, "days_remaining": days}


def currency_summary(
    profile: Any,
    entries: Any,
    today: _date | None = None,
    history: list[tuple[str | None, _date]] | None = None,
) -> dict[str, Any] | None:
    """
    Aggregate all currency checks. Returns None if profile is None.

    *entries* need only cover the last STATS_WINDOW_DAYS (recent_flights
    loads just those); *history* is flown_history().
    """
    if profile is None:
        return None
    if today is None:
        today = _date.today()

    pax = passenger_currency(entries, today)
    nite = night_currency(entries, today)
    med = medical_status(profile, today)
    sep = sep_status(profile, today)
    per_class = per_class_currency(entries, today, history)

    statuses = [pax["status"], nite["status"], med["status"], sep["status"]]
    if STATUS_EXPIRED in statuses:
        overall = STATUS_EXPIRED
    elif STATUS_WARNING in statuses or STATUS_UNKNOWN in statuses:
        overall = STATUS_WARNING
    else:
        overall = STATUS_OK

    return {
        "passenger": pax,
        "night": nite,
        "medical": med,
        "sep": sep,
        "per_class": per_class,
        "recent_aircraft": recent_aircraft(entries, today),
        "overall": overall,
    }
