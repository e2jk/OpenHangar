"""Where next? on its own page: open airfields not yet visited, within a
distance, still-air time or theoretical range of a reference airfield,
across the user's passports (Phase 42f).

Results are airfields, deduplicated, each with its reasons ("Not yet stamped
in Belgian passport 2026"), sorted by distance and, at equal distance, by
more reasons first: no opaque scoring. Sources are passports (open targets
not yet stamped) and Places (airfields linked to places in the user's
collections, not yet visited unless asked). Privacy is unchanged: each passport
contributes only the viewer's own progress, group passports only their
targets, and places only through the user's own collection memberships.
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
from models import Passport, PilotProfile, PlaceCollection, db
from passports import builder, discovery, waypoints
from passports.access import accessible_passports, require_passport
from passports.catalogue_access import visible_identity_ids
from passports.eligibility import participant_for, passport_progress
from passports.management import BuilderError
from places.access import PlaceError, collections_for_user
from places.airfields import linked_airfields
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
    "source",
    "visited",
    "place",
    "tag",
    "never_landed",
)


def _active_passports() -> list[Passport]:
    return list(
        accessible_passports()
        .filter(Passport.archived_at.is_(None))
        .order_by(Passport.title, Passport.id)
        .all()
    )


# The query parameter naming the passports to search. Not "passport": URL
# scanners take any parameter whose name contains "pass" for a password in the
# URL (ZAP rule 10024). Links and the form use this name; the old one is still
# read, so existing bookmarks keep working.
PASSPORT_PARAM = "stamp_book"
_LEGACY_PASSPORT_PARAM = "passport"


def _chosen(passports: list[Passport]) -> list[Passport]:
    """The passports to search: those ticked (only the user's own active
    ones count), else all of them."""
    wanted = {
        pid
        for name in (PASSPORT_PARAM, _LEGACY_PASSPORT_PARAM)
        for pid in request.args.getlist(name, type=db_id)
        if pid is not None
    }
    chosen = [p for p in passports if p.id in wanted]
    return chosen or passports


def _passport_reasons(
    passports: list[Passport], user_id: int
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """Each open airfield not yet visited, with the passports where it is
    still to stamp; and every such target (for the waypoint count)."""
    merged: dict[int, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    for passport in passports:
        for row in passport_progress(passport, user_id)["rows"]:
            if row["visited"] or row.get("is_closed"):
                continue
            pending.append(row)
            reason = {
                "kind": "passport",
                "passport": passport.title,
                "note": "",
                "url": url_for(
                    "passports.target_detail",
                    passport_id=passport.id,
                    target_id=row["target_id"],
                ),
            }
            if row["id"] in merged:
                merged[row["id"]]["reasons"].append(reason)
            else:
                merged[row["id"]] = {**row, "reasons": [reason]}
    return merged, pending


def _place_reasons(
    merged: dict[int, dict[str, Any]], links: list[dict[str, Any]]
) -> None:
    """Add the airfields linked to the chosen places, each place a reason."""
    for link in links:
        if link["is_closed"]:
            continue
        reason = {
            "kind": "place",
            "place_id": link["place_id"],
            "place_latitude": link["place_latitude"],
            "place_longitude": link["place_longitude"],
            "place": link["place_name"],
            "distance_km": link["distance_km"],
            "note": link["note"],
            "url": url_for("places.place_detail", place_id=link["place_id"]),
        }
        row = merged.get(link["airfield_id"])
        if row is None:
            merged[link["airfield_id"]] = {
                "id": link["airfield_id"],
                "key": link["key"],
                "name": link["name"],
                "ident": link["ident"],
                "latitude": link["latitude"],
                "longitude": link["longitude"],
                "visited": False,
                "reasons": [reason],
            }
        else:
            row["reasons"].append(reason)


def _tag_names(collection_ids: list[int]) -> list[str]:
    from models import PlaceTag

    return sorted(
        {
            str(name)
            for (name,) in db.session.query(PlaceTag.name).filter(
                PlaceTag.collection_id.in_(collection_ids)
            )
        },
        key=str.casefold,
    )


def _tagged_places(collection_ids: list[int], tag: str) -> list[int]:
    from models import Place, PlaceTag, PlaceTagAssignment

    return [
        pid
        for (pid,) in db.session.query(PlaceTagAssignment.place_id)
        .join(PlaceTag, PlaceTag.id == PlaceTagAssignment.tag_id)
        .join(Place, Place.id == PlaceTagAssignment.place_id)
        .filter(
            PlaceTag.collection_id.in_(collection_ids),
            db.func.lower(PlaceTag.name) == tag.casefold(),
        )
    ]


def _landed_codes(user_id: int) -> set[str]:
    """Codes of every airfield the user ever arrived at, as pilot or crew."""
    from models import Flight

    return {
        str(code).strip().upper()
        for (code,) in db.session.query(Flight.arrival_icao).filter(
            db.or_(
                Flight.pic_user_id == user_id, Flight.second_crew_user_id == user_id
            ),
            Flight.arrival_icao.isnot(None),
        )
    }


def _map_places(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The located places the result airfields serve, each with the
    airfields it links to among the results (lines on the map)."""
    places: dict[int, dict[str, Any]] = {}
    for row in rows:
        for reason in row["reasons"]:
            if reason["kind"] != "place" or reason["place_latitude"] is None:
                continue
            place = places.setdefault(
                reason["place_id"],
                {
                    "name": reason["place"],
                    "latitude": float(reason["place_latitude"]),
                    "longitude": float(reason["place_longitude"]),
                    "url": reason["url"],
                    "airfields": [],
                },
            )
            place["airfields"].append([float(row["latitude"]), float(row["longitude"])])
    return list(places.values())


def _unlinked(tenant_id: int, collection_ids: list[int]) -> int:
    """Active places in the chosen collections with no airfield yet."""
    from models import Place, PlaceAirfield

    linked = db.select(PlaceAirfield.place_id).where(
        PlaceAirfield.tenant_id == tenant_id
    )
    return int(
        Place.query.filter(
            Place.collection_id.in_(collection_ids),
            Place.deleted_at.is_(None),
            Place.archived.is_(False),
            Place.id.not_in(linked),
        ).count()
    )


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
    try:
        collections = (
            collections_for_user(tenant_id, uid)
            .order_by(PlaceCollection.name, PlaceCollection.id)
            .all()
        )
    except PlaceError:
        collections = []
    wanted = {
        cid for cid in request.args.getlist("collection", type=db_id) if cid is not None
    }
    chosen_collections = [c for c in collections if c.id in wanted] or collections
    place_id = request.args.get("place", type=db_id)
    sources = [
        name
        for name, present in (("places", collections), ("passports", passports))
        if present
    ]
    # Opened from a place or a passport, that source is preselected.
    source = request.args.get("source") or (
        "places"
        if place_id is not None
        else "passports"
        if request.args.get(PASSPORT_PARAM) or request.args.get(_LEGACY_PASSPORT_PARAM)
        else "both"
        if len(sources) == 2
        else sources[0]
        if sources
        else "passports"
    )
    if source not in {"places", "passports", "both"}:
        source = "both"
    include_visited = request.args.get("visited") == "1"
    never_landed = request.args.get("never_landed") == "1"
    tag = (request.args.get("tag") or "").strip()[:80]
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
    merged, pending = (
        _passport_reasons(chosen, uid) if source in {"passports", "both"} else ({}, [])
    )
    unlinked = 0
    tags: list[str] = []
    if source in {"places", "both"} and chosen_collections:
        collection_ids = [c.id for c in chosen_collections]
        tags = _tag_names(collection_ids)
        place_ids = [place_id] if place_id is not None else None
        if tag:
            tagged = set(_tagged_places(collection_ids, tag))
            place_ids = [p for p in (place_ids or tagged) if p in tagged]
        _place_reasons(
            merged,
            linked_airfields(
                tenant_id,
                uid,
                collection_ids,
                include_visited=include_visited,
                place_ids=place_ids,
            ),
        )
        if place_id is None and not tag:
            unlinked = _unlinked(tenant_id, collection_ids)
    candidates = list(merged.values())
    if never_landed:
        landed = _landed_codes(uid)
        candidates = [
            row
            for row in candidates
            if not {
                str(code).upper()
                for code in (row.get("ident"), row.get("icao_code"))
                if code
            }
            & landed
        ]
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
        query += [(PASSPORT_PARAM, str(p.id)) for p in chosen]
    if len(chosen_collections) != len(collections):
        query += [("collection", str(c.id)) for c in chosen_collections]
    waypoint_links = (
        {
            fmt: url_for("explore.where_next")
            + "?"
            + urlencode([*query, ("download", fmt)])
            for fmt in waypoints.FORMATS
        }
        if enabled and selection.points
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
        "places": _map_places(rows),
    }
    return render_template(
        "explore/where_next.html",
        passports=passports,
        chosen_ids={p.id for p in chosen},
        collections=collections,
        chosen_collection_ids={c.id for c in chosen_collections},
        sources=sources,
        source=source,
        include_visited=include_visited,
        place_id=place_id,
        unlinked=unlinked,
        tags=tags,
        tag=tag,
        never_landed=never_landed,
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
        + urlencode([(PASSPORT_PARAM, str(passport_id)), *query])
    )
    return redirect(target)
