"""Where next? on its own page: open airfields not yet visited, within a
distance, still-air time or theoretical range of a reference airfield,
across the user's passports (Phase 42f).

Results are airfields, deduplicated, each with its reasons ("Not yet stamped
in Belgian passport 2026"), sorted by distance and, at equal distance, by
more reasons first: no opaque scoring. Places join as a second source once
airfields can be linked to places (42d). Privacy is the passport page's: each
passport contributes only the viewer's own progress, and group passports only
their targets.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from flask import (
    Blueprint,
    Response,
    abort,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue
from flask_babel import gettext as _
from models import Passport, PilotProfile
from passports import builder, discovery, waypoints
from passports.access import accessible_passports, require_passport
from passports.catalogue_access import visible_identity_ids
from passports.eligibility import participant_for, passport_progress
from passports.management import BuilderError
from services import destination_search as search
from utils import accessible_aircraft, current_membership, db_id, login_required

explore_bp = Blueprint("explore", __name__, url_prefix="/explore")

# The page's own inputs: the only query keys ever echoed into its links.
QUERY_KEYS = (
    "aircraft",
    "origin",
    "q",
    "start",
    "mode",
    "speed",
    "limit",
    "usable_fuel",
    "flow",
    "reserve",
    "range",
    "trip",
)


def _active_passports() -> list[Passport]:
    return list(
        accessible_passports()
        .filter(Passport.archived_at.is_(None))
        .order_by(Passport.title, Passport.id)
        .all()
    )


def _chosen(passports: list[Passport]) -> list[Passport]:
    """The passports to search: those ticked (only the user's own active
    ones count), else all of them."""
    wanted = {
        pid for pid in request.args.getlist("passport", type=db_id) if pid is not None
    }
    chosen = [p for p in passports if p.id in wanted]
    return chosen or passports


def _candidates(
    passports: list[Passport], user_id: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Each open airfield not yet visited, once, with the passports where it
    is still to stamp; and every such target (for the waypoint count)."""
    merged: dict[str, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    for passport in passports:
        for row in passport_progress(passport, user_id)["rows"]:
            if row["visited"] or row.get("is_closed"):
                continue
            pending.append(row)
            reason = {
                "passport_id": passport.id,
                "passport": passport.title,
                "url": url_for(
                    "passports.target_detail",
                    passport_id=passport.id,
                    target_id=row["target_id"],
                ),
            }
            if row["key"] in merged:
                merged[row["key"]]["reasons"].append(reason)
            else:
                merged[row["key"]] = {**row, "reasons": [reason]}
    return list(merged.values()), pending


@explore_bp.route("/where-next")
@login_required
def where_next() -> ResponseReturnValue:
    membership = current_membership()
    if membership is None:
        abort(403)
    tenant_id = membership.tenant_id
    uid = session["user_id"]
    passports = _active_passports()
    chosen = _chosen(passports)
    aircraft = (
        []
        if visible_identity_ids(tenant_id) is not None
        else accessible_aircraft(tenant_id).all()
    )
    selected = None
    if request.args.get("aircraft"):
        selected = next(
            (
                ac
                for ac in aircraft
                if ac.id == request.args.get("aircraft", type=db_id)
            ),
            None,
        )
        if selected is None:
            abort(404)
    profile = PilotProfile.query.filter_by(user_id=uid).first()
    home = discovery.home_descriptor(selected, tenant_id) or discovery.home_descriptor(
        profile, tenant_id
    )
    # A passport's excluded arrivals only apply when it is searched alone.
    participant = participant_for(chosen[0].id, uid) if len(chosen) == 1 else None
    latest = discovery.latest_arrival(
        tenant_id, uid, participant.id if participant else None
    )
    searches = discovery.origin_choices(tenant_id, request.args.get("q", "")[:100])
    origin_key = request.args.get("origin") or (home["key"] if home else "")
    if request.args.get("start") == "latest" and latest:
        origin_key = latest["key"]
    if request.args.get("start") == "home" and home:
        origin_key = home["key"]
    origin = None
    errors = []
    if origin_key:
        origin = builder.available_fields(tenant_id).get(origin_key)
        if origin is None:
            errors.append(_("Choose a reference airfield from the search results."))
        elif origin.get("latitude") is None or origin.get("longitude") is None:
            errors.append(
                _("This reference airfield has no coordinates. Choose another.")
            )
    mode = request.args.get("mode", "distance")
    if mode not in {"distance", "time"}:
        mode = "distance"
    defaults = {
        "speed": str(selected.cruise_speed_knots)
        if selected and selected.cruise_speed_knots is not None
        else "",
        "limit": "100",
        "usable_fuel": "",
        "flow": str(selected.fuel_flow)
        if selected and selected.fuel_flow is not None
        else "",
        "reserve": "",
    }
    preference = discovery.reserve_preference(uid)
    if preference is not None:
        defaults["reserve"] = str(preference)
    values = {
        key: (request.args.get(key) or value)[:100] for key, value in defaults.items()
    }
    parsed = {}
    for key, label, maximum in [
        ("speed", _("cruise speed"), 9999.99),
        ("limit", _("distance/time limit"), 21600),
        ("usable_fuel", _("usable fuel"), 999999),
        ("flow", _("fuel flow"), 9999.99),
        ("reserve", _("reserve minutes"), 1440),
    ]:
        try:
            parsed[key] = discovery.positive(
                values[key], label, maximum, zero=key == "reserve"
            )
        except BuilderError as exc:
            parsed[key] = None
            errors.append(str(exc))
    limit = parsed["limit"] or 100
    speed = parsed["speed"]
    if mode == "time" and speed is None:
        errors.append(_("Enter a cruise speed for time mode, or use distance mode."))
    returning = request.args.get("trip") == "return"
    range_nm = discovery.theoretical_range(
        speed, parsed["usable_fuel"], parsed["flow"], parsed["reserve"], returning
    )
    range_enabled = request.args.get("range") == "1"
    if range_enabled and range_nm is None:
        errors.append(
            _(
                "Theoretical range is unknown. Enter usable fuel, fuel flow, speed and an explicit reserve leaving positive flight time. The distance/time filter still applies."
            )
        )
    candidates, pending = _candidates(chosen, uid)
    rows, missing = search.nearby(
        candidates,
        origin,
        speed,
        limit,
        mode,
        range_nm if range_enabled else None,
        tie_breaker=lambda row: row["key"],
    )
    # At equal distance, more reasons first (the sort is stable).
    rows.sort(key=lambda row: (row["distance_nm"], -len(row["reasons"])))

    enabled = origin is not None and not errors
    selection = waypoints.select(rows, missing_rows=pending)
    fmt = request.args.get("download")
    if fmt is not None:
        if not enabled:
            abort(
                400,
                description=_(
                    "Choose a reference airfield and correct the destination filters before downloading."
                ),
            )
        if fmt not in waypoints.FORMATS:
            abort(400)
        response = Response(
            waypoints.document(selection, fmt), mimetype=waypoints.FORMATS[fmt]
        )
        response.headers["Content-Disposition"] = (
            f'attachment; filename="where-next-unvisited.{fmt}"'
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    # Never pass arbitrary query keys to url_for: its reserved options can
    # change the URL's host or scheme. These are the page's own inputs.
    query: list[tuple[str, str]] = [
        (key, request.args[key][:100]) for key in QUERY_KEYS if key in request.args
    ]
    if len(chosen) != len(passports):
        query += [("passport", str(p.id)) for p in chosen]
    waypoint_links = (
        {
            fmt: url_for("explore.where_next")
            + "?"
            + urlencode([*query, ("download", fmt)])
            for fmt in waypoints.FORMATS
        }
        if enabled
        else {}
    )

    radius = (
        limit * speed / 60
        if mode == "time" and speed
        else limit
        if mode == "distance"
        else None
    )
    if radius is not None and range_enabled and range_nm is not None:
        radius = min(radius, range_nm)
    map_data = {
        "origin": {
            key: float(origin[key])
            if key in {"latitude", "longitude"} and origin.get(key) is not None
            else origin.get(key)
            for key in ("name", "latitude", "longitude")
        }
        if origin
        else None,
        "radius_nm": radius,
        "fields": [
            {
                "name": row["name"],
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "distance_nm": row["distance_nm"],
                "url": row["reasons"][0]["url"],
            }
            for row in rows
        ],
    }
    return render_template(
        "explore/where_next.html",
        passports=passports,
        chosen_ids={p.id for p in chosen},
        aircraft=aircraft,
        selected=selected,
        home=home,
        latest=latest,
        origin_options=list(
            {
                field["key"]: field
                for field in [origin, home, latest, *searches]
                if field is not None
            }.values()
        ),
        origin=origin,
        origin_key=origin_key,
        rows=rows,
        missing=missing,
        values=values,
        mode=mode,
        range_enabled=range_enabled,
        returning=returning,
        range_nm=range_nm,
        map_data=map_data,
        errors=errors,
        preference=preference,
        waypoint_links=waypoint_links,
        omitted_waypoints=selection.omitted,
    )


def redirect_from_passport(passport_id: int) -> ResponseReturnValue:
    """The old per-passport page: the same search, that passport preselected."""
    require_passport(passport_id)
    query = [
        (key, request.args[key][:100]) for key in QUERY_KEYS if key in request.args
    ]
    if "download" in request.args:
        query.append(("download", request.args["download"][:10]))
    target = (
        url_for("explore.where_next")
        + "?"
        + urlencode([("passport", str(passport_id)), *query])
    )
    return redirect(target)
