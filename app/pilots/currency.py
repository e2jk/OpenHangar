from datetime import date as _date, timedelta
from typing import Any

WINDOW_DAYS = 90
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


def per_type_currency(entries: Any, today: _date | None = None) -> dict[str, Any]:
    """Rolling 90-day landing currency grouped by ICAO aircraft type.

    EASA FCL.060 per type:
    - Passenger carry: 3 landings (day OR night combined) in 90 days.
    - Night passenger carry: 1 night landing in 90 days.

    Entries whose aircraft_type_icao is blank are resolved on-the-fly from
    aircraft_type via resolve_aircraft_type_icao(). Those that still cannot be
    resolved are tallied in unresolved_count so the UI can surface a warning.

    Returns::

        {
            "by_type": {
                "C172": {"passenger": {...}, "night": {...}, "status": "ok"},
                "P28A": {"passenger": {...}, "night": {...}, "status": "warning"},
            },
            "unresolved_count": 3,
        }
    """
    if today is None:
        today = _date.today()

    from utils import resolve_aircraft_type_icao  # pyright: ignore[reportMissingImports]

    buckets: dict[str, list[Any]] = {}
    unresolved_count = 0

    for entry in entries:
        icao: str | None = getattr(entry, "aircraft_type_icao", None) or None
        if not icao:
            icao = resolve_aircraft_type_icao(getattr(entry, "aircraft_type", None))
        if not icao:
            unresolved_count += 1
            continue
        buckets.setdefault(icao, []).append(entry)

    by_type: dict[str, dict[str, Any]] = {}
    for icao, type_entries in sorted(buckets.items()):
        pax = _rolling_landing_currency(
            type_entries,
            ("landings_day", "landings_night"),
            PASSENGER_REQUIRED,
            today,
        )
        night = _rolling_landing_currency(
            type_entries, "landings_night", NIGHT_REQUIRED, today
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
        by_type[icao] = {"passenger": pax, "night": night, "status": status}

    return {"by_type": by_type, "unresolved_count": unresolved_count}


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
    expiry = profile.medical_expiry if profile else None
    status, days = _expiry_status(expiry, today, EXPIRY_WARN_DAYS)
    return {"expiry": expiry, "status": status, "days_remaining": days}


def sep_status(profile: Any, today: _date | None = None) -> dict[str, Any]:
    if today is None:
        today = _date.today()
    expiry = profile.sep_expiry if profile else None
    status, days = _expiry_status(expiry, today, EXPIRY_WARN_DAYS)
    return {"expiry": expiry, "status": status, "days_remaining": days}


def currency_summary(
    profile: Any, entries: Any, today: _date | None = None
) -> dict[str, Any] | None:
    """
    Aggregate all currency checks. Returns None if profile is None.
    """
    if profile is None:
        return None
    if today is None:
        today = _date.today()

    pax = passenger_currency(entries, today)
    nite = night_currency(entries, today)
    med = medical_status(profile, today)
    sep = sep_status(profile, today)
    per_type = per_type_currency(entries, today)

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
        "per_type": per_type,
        "overall": overall,
    }
