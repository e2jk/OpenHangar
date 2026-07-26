"""Phase 39a: validation for the shared-ownership manage-owners form.

Extracted from the aircraft.manage_owners route (following the same
pattern as maintenance/form_parsing.py) so the dynamic-rows / sum-to-100
validation can be unit-tested and fuzzed directly. Never raises on
arbitrary form data.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date as _date
from decimal import Decimal, InvalidOperation
from typing import Any

from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

TWO_PLACES_EXPONENT = -2


def _parse_decimal(raw: str) -> Decimal | None:
    """Decimal("inf")/Decimal("nan") parse without raising, so an explicit
    finiteness check is required alongside the sign/range checks below."""
    try:
        v = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return v if v.is_finite() else None


def _exceeds_two_decimal_places(d: Decimal) -> bool:
    exponent = d.as_tuple().exponent
    # Only ever a non-int ('n'/'N'/'F') for nan/snan/infinity, already
    # excluded by _parse_decimal's is_finite() check — isinstance narrows
    # the type for mypy rather than asserting something new.
    return isinstance(exponent, int) and exponent < TWO_PLACES_EXPONENT


def parse_owners_form(
    form: Any,
) -> tuple[list[dict[str, Any]], _date | None, Decimal | None, list[str]]:
    """Parse the manage-owners form.

    `form` must support `.get(name, default)` and `.getlist(name)` (an
    ``ImmutableMultiDict`` in production, a plain dict-like stub in tests).

    Returns (rows, billing_start, hourly_rate, errors). `rows` is a list of
    {"user_id": int, "share_pct": Decimal, "buy_in_amount": Decimal} dicts;
    a row is skipped — "removed" — when it has no user selected (the blank
    template rows) or its "remove" checkbox is checked (existing rows).
    The caller replaces the aircraft's entire owner set with exactly these
    rows; zero rows is valid (clears co-ownership).
    """
    errors: list[str] = []
    user_ids_raw: Sequence[str] = form.getlist("owner_user_id[]")
    share_raw: Sequence[str] = form.getlist("owner_share_pct[]")
    buyin_raw: Sequence[str] = form.getlist("owner_buy_in_amount[]")
    # Checkbox values are the row index — mirrors the WB-config station
    # pattern (station_is_fuel[]): unchecked boxes never submit at all, so
    # this list only ever contains the indices of *checked* rows.
    remove_indices = set(form.getlist("owner_remove[]"))

    rows: list[dict[str, Any]] = []
    seen_users: set[int] = set()

    for i, uid_s in enumerate(user_ids_raw):
        uid_s = uid_s.strip()
        if not uid_s or str(i) in remove_indices:
            continue
        try:
            uid = int(uid_s)
        except ValueError:
            errors.append(_("Invalid owner selection."))
            continue
        if uid in seen_users:
            errors.append(_("Each owner can only appear once."))
            continue

        share_s = (share_raw[i] if i < len(share_raw) else "").strip()
        share = _parse_decimal(share_s)
        if (
            share is None
            or share <= 0
            or share > 100
            or _exceeds_two_decimal_places(share)
        ):
            errors.append(
                _(
                    "Share for each owner must be greater than 0, at most 100, "
                    "and have at most 2 decimal places."
                )
            )
            continue

        buyin_s = (buyin_raw[i] if i < len(buyin_raw) else "").strip()
        buy_in = _parse_decimal(buyin_s) if buyin_s else Decimal("0")
        if buy_in is None or buy_in < 0:
            errors.append(_("Buy-in amount must be a non-negative number."))
            continue

        seen_users.add(uid)
        rows.append({"user_id": uid, "share_pct": share, "buy_in_amount": buy_in})

    if rows:
        total = sum((r["share_pct"] for r in rows), Decimal("0"))
        if total != Decimal("100"):
            errors.append(_("Share percentages must sum to exactly 100%%."))

    billing_start_raw = (form.get("co_owner_billing_start", "") or "").strip()
    billing_start: _date | None = None
    if billing_start_raw:
        try:
            billing_start = _date.fromisoformat(billing_start_raw)
        except ValueError:
            errors.append(_("Billing start date must be a valid date (YYYY-MM-DD)."))

    rate_raw = (form.get("co_owner_hourly_rate", "") or "").strip()
    rate: Decimal | None = None
    if rate_raw:
        rate = _parse_decimal(rate_raw)
        if rate is None or rate < 0:
            errors.append(_("Hourly rate must be a non-negative number."))
            rate = None

    return rows, billing_start, rate, errors
