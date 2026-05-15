from datetime import date as _date, timedelta
from typing import Any

WINDOW_DAYS = 90
PASSENGER_REQUIRED = 3
NIGHT_REQUIRED = 3
EXPIRY_WARN_DAYS = 90  # warn when medical/SEP expires within this many days
CURRENCY_WARN_DAYS = 30  # warn when landing currency expires within this many days

STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_EXPIRED = "expired"
STATUS_UNKNOWN = "unknown"


def _rolling_landing_currency(
    entries: Any, landing_field: str, required: int, today: _date
) -> dict[str, Any]:
    window_start = today - timedelta(days=WINDOW_DAYS)
    qualifying = [
        e
        for e in entries
        if e.date >= window_start and (getattr(e, landing_field) or 0) > 0
    ]
    qualifying.sort(key=lambda e: (e.date, e.id), reverse=True)

    total = sum(getattr(e, landing_field) or 0 for e in qualifying)
    shortfall = max(0, required - total)

    if total >= required:
        cum = 0
        anchor_date: _date | None = None
        for e in qualifying:
            cum += getattr(e, landing_field) or 0
            if cum >= required:
                anchor_date = e.date
                break
        assert anchor_date is not None
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


def passenger_currency(entries: Any, today: _date | None = None) -> dict[str, Any]:
    """3 day landings in rolling 90-day window (EASA PPL passenger carry)."""
    if today is None:
        today = _date.today()
    return _rolling_landing_currency(entries, "landings_day", PASSENGER_REQUIRED, today)


def night_currency(entries: Any, today: _date | None = None) -> dict[str, Any]:
    """3 night landings in rolling 90-day window."""
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
        "overall": overall,
    }
