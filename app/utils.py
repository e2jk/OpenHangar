"""Shared utilities available to all blueprints."""

import csv
import functools
import io
import logging
import math
import os
from collections import defaultdict
from functools import wraps
from typing import Any, Callable

from flask import abort, redirect, session, url_for  # pyright: ignore[reportMissingImports]

_log = logging.getLogger(__name__)


# ── Tracks GIF export ─────────────────────────────────────────────────────────

_GIF_W, _GIF_H = 800, 480
_GIF_PAD = 30  # pixel padding around the track area
_GIF_STEP_MS = 600  # ms per frame in the animated GIF
_GIF_HOLD_MS = 3000  # ms for the final "all tracks" frame
_TRACK_COLOUR = (180, 30, 200)  # current/newest track: vivid purple
_HISTORY_COLOUR = (
    130,
    20,
    150,
)  # previously drawn tracks: slightly darker purple (no fade)
_BG_COLOUR = (248, 248, 252)  # near-white background


def _mercator_y(lat_deg: float) -> float:
    """Web-Mercator y value for a latitude (not clamped)."""
    lat = math.radians(max(-85.0, min(85.0, lat_deg)))
    return math.log(math.tan(math.pi / 4 + lat / 2))


def _build_gif_projection(
    all_coords: list[tuple[float, float]],
) -> "tuple[Any, Any] | None":
    """Return (project_fn, bbox) or None if there are fewer than 2 unique points."""
    if len(all_coords) < 2:
        return None
    lons = [c[0] for c in all_coords]
    lats = [c[1] for c in all_coords]
    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)
    # Add small margin so tracks don't touch the edge
    dlon = max(max_lon - min_lon, 0.1) * 0.1
    dlat = max(max_lat - min_lat, 0.1) * 0.1
    min_lon -= dlon
    max_lon += dlon
    min_lat -= dlat
    max_lat += dlat

    usable_w = _GIF_W - 2 * _GIF_PAD
    usable_h = _GIF_H - 2 * _GIF_PAD

    y_min = _mercator_y(min_lat)
    y_max = _mercator_y(max_lat)
    y_range = y_max - y_min or 1e-9  # Mercator log-tan units (NOT degrees)
    x_range = max_lon - min_lon or 1e-9  # degrees

    # Web Mercator geographic aspect: scale_y_mercator = scale_x * 180/π
    # (1 Mercator-y unit = 111 km at any latitude; 1° lon at centre = cos(φ)*111 km,
    # so equal-km scales satisfy scale_y = scale_x * 180/π).
    # Pick scale_x so BOTH axes fit: scale_x ≤ w/x_range AND scale_x * 180/π * y_range ≤ h.
    scale_x = min(
        usable_w / x_range,
        usable_h * math.pi / (180.0 * y_range),
    )
    scale_y = scale_x * 180.0 / math.pi  # pixels per Mercator-y unit

    off_x = _GIF_PAD + (usable_w - x_range * scale_x) / 2
    off_y = _GIF_PAD + (usable_h - y_range * scale_y) / 2

    def project(lon: float, lat: float) -> tuple[int, int]:
        px = off_x + (lon - min_lon) * scale_x
        py = off_y + (y_max - _mercator_y(lat)) * scale_y
        return int(px), int(py)

    return project, (min_lon, min_lat, max_lon, max_lat)


def _coords_from_geojson(geojson: dict[str, Any] | None) -> list[tuple[float, float]]:
    """Extract (lon, lat) pairs from a GeoJSON Feature or FeatureCollection."""
    if not geojson:
        return []
    if geojson.get("type") == "Feature":
        geom = geojson.get("geometry") or {}
        return [(c[0], c[1]) for c in geom.get("coordinates", []) if len(c) >= 2]
    if geojson.get("type") == "FeatureCollection":
        result = []
        for feat in geojson.get("features", []):
            result.extend(_coords_from_geojson(feat))
        return result
    return []


def _make_tile_background(
    project: Callable[[float, float], tuple[int, int]],
    min_lon: float,
    max_lon: float,
    min_lat: float,
    max_lat: float,
    canvas_w: int,
    canvas_h: int,
    openaip_key: str | None = None,
) -> Any:
    """Fetch OSM raster tiles and composite them into a background PIL Image.

    Returns a PIL Image, or None on failure (caller falls back to plain fill).
    Tiles are fetched from OpenStreetMap; zoom level is chosen automatically.
    Max 36 tiles are fetched to keep export time reasonable.
    """
    import urllib.request
    from PIL import Image as _Img  # pyright: ignore[reportMissingImports]

    # Compute pixels-per-degree-lon from the projection function
    mid_lat = (min_lat + max_lat) / 2.0
    px0x = project(min_lon, mid_lat)[0]
    px1x = project(min_lon + 1.0, mid_lat)[0]
    scale_x = float(px1x - px0x)  # canvas pixels per degree-lon
    if scale_x <= 0:
        return None

    # Choose zoom so each tile is ~256 canvas pixels wide (clamped 2–14)
    z = int(round(math.log2(max(scale_x * 360.0 / 256.0, 1.0))))
    z = max(2, min(z, 14))
    n = 2**z
    tile_px = max(1, round(scale_x * 360.0 / n))  # canvas pixels per tile

    def _lon_to_tx(lon: float) -> int:
        return int(int((lon + 180.0) / 360.0 * n) % n)

    def _lat_to_ty(lat: float) -> int:
        lat_r = math.radians(max(-85.0, min(85.0, lat)))
        return int(
            (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi)
            / 2.0
            * n
        )

    def _tile_nw_lonlat(tx: int, ty: int) -> tuple[float, float]:
        lon = tx * 360.0 / n - 180.0
        lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * ty / n))))
        return lon, lat

    tx_min = _lon_to_tx(min_lon)
    tx_max = _lon_to_tx(max_lon)
    ty_min = _lat_to_ty(max_lat)
    ty_max = _lat_to_ty(min_lat)

    # Guard against tile count explosion
    if (tx_max - tx_min + 1) * (ty_max - ty_min + 1) > 36:
        return None

    bg = _Img.new("RGB", (canvas_w, canvas_h), _BG_COLOUR)
    ua = "OpenHangar flight-logbook GIF export (https://github.com/e2jk/OpenHangar)"

    for tx in range(tx_min, tx_max + 1):
        for ty in range(ty_min, ty_max + 1):
            try:
                # Base map: CARTO light (same as web animation)
                tx_w = tx % n
                base_url = (
                    f"https://a.basemaps.cartocdn.com/light_all/{z}/{tx_w}/{ty}.png"
                )
                req = urllib.request.Request(base_url, headers={"User-Agent": ua})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    tile = _Img.open(io.BytesIO(resp.read())).convert("RGBA")
                tile = tile.resize((tile_px, tile_px), _Img.Resampling.LANCZOS)
                lon, lat = _tile_nw_lonlat(tx, ty)
                px, py = project(lon, lat)
                bg.paste(tile.convert("RGB"), (px, py))
                # Aviation overlay: OpenAIP (only at zoom ≤ 14, requires key)
                if openaip_key and z <= 14:
                    opi_url = f"https://api.tiles.openaip.net/api/data/openaip/{z}/{tx_w}/{ty}.png?apiKey={openaip_key}"
                    opi_req = urllib.request.Request(
                        opi_url, headers={"User-Agent": ua}
                    )
                    with urllib.request.urlopen(opi_req, timeout=5) as opi_resp:
                        opi_tile = _Img.open(io.BytesIO(opi_resp.read())).convert(
                            "RGBA"
                        )
                    opi_tile = opi_tile.resize(
                        (tile_px, tile_px), _Img.Resampling.LANCZOS
                    )
                    bg.paste(opi_tile.convert("RGB"), (px, py), opi_tile)
            except Exception:
                pass  # leave that tile area as plain background colour

    return bg


def generate_tracks_gif(
    track_rows: list[dict[str, Any]],
    _font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    _openaip_key: str | None = None,
) -> bytes:
    """Render an animated GIF of the flight tracks, oldest-first.

    Each frame adds one more track (cumulative).  All previous tracks are
    drawn in a light colour; the newest one in vivid purple.  Returns raw
    GIF bytes ready to stream to the browser.
    """
    from PIL import Image, ImageDraw, ImageFont  # pyright: ignore[reportMissingImports]

    # Sort oldest-first for chronological playback
    sorted_rows = sorted(track_rows, key=lambda r: r.get("date", ""))

    # Collect all coordinates to build a single shared projection
    all_coords: list[tuple[float, float]] = []
    for row in sorted_rows:
        all_coords.extend(_coords_from_geojson(row.get("geojson")))

    proj_result = _build_gif_projection(all_coords)
    if proj_result is None:
        # Fallback: single blank frame
        img = Image.new("RGB", (_GIF_W, _GIF_H), _BG_COLOUR)
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        return buf.getvalue()

    project, (p_min_lon, p_min_lat, p_max_lon, p_max_lat) = proj_result

    # Fetch tile basemap once — reused for every frame
    tile_bg: Any = _make_tile_background(
        project,
        p_min_lon,
        p_max_lon,
        p_min_lat,
        p_max_lat,
        _GIF_W,
        _GIF_H,
        openaip_key=_openaip_key,
    )

    font: Any  # PIL font type varies across Pillow versions
    font_sm: Any
    try:
        font = ImageFont.truetype(_font_path, 14)
        font_sm = ImageFont.truetype(_font_path, 11)
    except (IOError, OSError):
        font = font_sm = ImageFont.load_default()

    def draw_track(
        draw: Any,
        coords: list[tuple[float, float]],
        colour: tuple[int, int, int],
        width: int,
    ) -> None:
        pts = [project(lon, lat) for lon, lat in coords]
        if len(pts) >= 2:
            draw.line(pts, fill=colour, width=width)
            # Start/end dots
            r = width + 2
            sx, sy = pts[0]
            draw.ellipse([sx - r, sy - r, sx + r, sy + r], fill=(40, 160, 60))
            ex, ey = pts[-1]
            draw.ellipse([ex - r, ey - r, ex + r, ey + r], fill=(200, 40, 40))

    def _base_frame() -> Any:
        """Return a fresh copy of the tile background (or plain fill if unavailable)."""
        if tile_bg is not None:
            return tile_bg.copy()
        return Image.new("RGB", (_GIF_W, _GIF_H), _BG_COLOUR)

    frames: list[Any] = []
    durations: list[int] = []

    for frame_idx in range(len(sorted_rows)):
        img = _base_frame()
        draw = ImageDraw.Draw(img)

        # Draw all earlier tracks in history colour
        for i in range(frame_idx):
            coords = _coords_from_geojson(sorted_rows[i].get("geojson"))
            draw_track(draw, coords, _HISTORY_COLOUR, 2)

        # Draw the current (newest) track in vivid colour
        coords = _coords_from_geojson(sorted_rows[frame_idx].get("geojson"))
        draw_track(draw, coords, _TRACK_COLOUR, 3)

        # Date label — white shadow for readability over the map
        row = sorted_rows[frame_idx]
        label = f"{row.get('date', '')}  {row.get('dep', '')} → {row.get('arr', '')}"
        draw.text((9, 9), label, fill=(255, 255, 255), font=font)
        draw.text((8, 8), label, fill=(40, 40, 60), font=font)
        draw.text(
            (8, _GIF_H - 22),
            f"{frame_idx + 1} / {len(sorted_rows)}",
            fill=(80, 80, 100),
            font=font_sm,
        )

        frames.append(img)
        durations.append(_GIF_STEP_MS)

    # Final frame: all tracks at equal weight, longer hold
    if frames:
        img = _base_frame()
        draw = ImageDraw.Draw(img)
        for row in sorted_rows:
            coords = _coords_from_geojson(row.get("geojson"))
            draw_track(draw, coords, _TRACK_COLOUR, 2)
        draw.text(
            (9, 9), f"All {len(sorted_rows)} tracks", fill=(255, 255, 255), font=font
        )
        draw.text(
            (8, 8), f"All {len(sorted_rows)} tracks", fill=(40, 40, 60), font=font
        )
        frames.append(img)
        durations.append(_GIF_HOLD_MS)

    # Quantise all frames to a shared 128-colour palette built from the first
    # frame.  Using a single global palette means identical background areas
    # compress very well with GIF's LZW codec (typically 5–10× size reduction
    # vs. per-frame palettes with a photographic tile background).
    palette_src = frames[0].quantize(colors=128, dither=Image.Dither.NONE)
    quantized: list[Any] = [palette_src] + [
        f.quantize(palette=palette_src, dither=Image.Dither.NONE) for f in frames[1:]
    ]

    buf = io.BytesIO()
    quantized[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=quantized[1:],
        loop=0,
        duration=durations,
        optimize=True,
    )
    return buf.getvalue()


@functools.lru_cache(maxsize=1)
def _load_aircraft_types() -> dict[str, tuple[str, str]]:
    """Return {type_designator: (manufacturer, model)} from aircraft_types.csv."""
    path = os.path.join(os.path.dirname(__file__), "data", "aircraft_types.csv")
    result: dict[str, tuple[str, str]] = {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                des = row.get("type_designator", "").strip().upper()
                mfr = row.get("manufacturer", "").strip()
                model = row.get("model", "").strip()
                if des:
                    result[des] = (mfr, model)
    except OSError as exc:
        _log.warning("aircraft_types.csv not found: %s", exc)
    return result


def _load_aircraft_type_variants() -> list[tuple[str, str]]:
    """Return all (type_designator, full_name) pairs — one per CSV row.

    Unlike _load_aircraft_types(), duplicate designators are preserved so
    the search endpoint can surface every variant (e.g. all PA-28-181 models
    that share the P28A ICAO code).
    """
    path = os.path.join(os.path.dirname(__file__), "data", "aircraft_types.csv")
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                des = row.get("type_designator", "").strip().upper()
                mfr = row.get("manufacturer", "").strip()
                model = row.get("model", "").strip()
                name = f"{mfr} {model}".strip()
                if des and (des, name) not in seen:
                    result.append((des, name))
                    seen.add((des, name))
    except OSError as exc:
        _log.warning("aircraft_types.csv not found: %s", exc)
    return result


@functools.lru_cache(maxsize=1)
def _load_aircraft_type_engine_data() -> dict[str, tuple[int, str]]:
    """Return {type_designator: (engine_count, engine_type)} from aircraft_types.csv.

    For designators with multiple rows (variants), uses data from the first row.
    """
    path = os.path.join(os.path.dirname(__file__), "data", "aircraft_types.csv")
    result: dict[str, tuple[int, str]] = {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                des = row.get("type_designator", "").strip().upper()
                if not des or des in result:
                    continue
                try:
                    ec = int(row.get("engine_count", "1"))
                except ValueError:
                    ec = 1
                et = row.get("engine_type", "").strip()
                result[des] = (ec, et)
    except OSError as exc:
        _log.warning("aircraft_types.csv not found: %s", exc)
    return result


def get_aircraft_type_engine_info(icao_code: str) -> tuple[int, str] | None:
    """Return (engine_count, engine_type) for the given ICAO code, or None."""
    return _load_aircraft_type_engine_data().get(icao_code.strip().upper())


def resolve_aircraft_type_icao(aircraft_type: str | None) -> str | None:
    """Return the matching ICAO type designator for *aircraft_type*, or None."""
    if not aircraft_type:
        return None
    types = _load_aircraft_types()
    norm = aircraft_type.strip().upper()
    if norm in types:
        return norm
    # Try stripping hyphens and spaces (e.g. "PA-28" → "PA28")
    compact = norm.replace("-", "").replace(" ", "")
    if compact in types:
        return compact
    return None


@functools.lru_cache(maxsize=1)
def _load_airport_names() -> dict[str, str]:
    """Return {ICAO ident: airport name} for all airports in airports.csv."""
    path = os.path.join(os.path.dirname(__file__), "data", "airports.csv")
    result: dict[str, str] = {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ident = row.get("ident", "").strip()
                name = row.get("name", "").strip()
                if ident and name:
                    result[ident] = name
    except OSError as exc:
        _log.warning("airports.csv not found: %s", exc)
    return result


_alog = logging.getLogger("openhangar.activity")


def _sl(value: object) -> str:
    """Sanitize a value for log output — strips CR/LF to prevent log injection (CWE-117)."""
    return str(value).replace("\r\n", "").replace("\n", "").replace("\r", "")


def activity(event: str, **fields: object) -> None:
    """Emit a structured [ACTIVITY] log entry with user_id and ip automatically included."""
    from flask import request, session  # noqa: PLC0415

    uid = session.get("user_id", "")
    ip = request.remote_addr or ""
    parts = [f"[ACTIVITY] {event}", f"user_id={_sl(uid)}", f"ip={_sl(ip)}"]
    parts.extend(f"{k}={_sl(v)}" for k, v in fields.items())
    _alog.info(" ".join(parts))


def login_required(f: Callable[..., Any]) -> Callable[..., Any]:
    """Redirect unauthenticated users to the login page."""

    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        if not session.get("user_id"):
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated


def require_instance_admin(f: Callable[..., Any]) -> Callable[..., Any]:
    """Abort 403 unless the current user is the instance admin."""

    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        from models import User, db

        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("auth.login"))
        user = db.session.get(User, user_id)
        if not user or not user.is_instance_admin:
            abort(403)
        return f(*args, **kwargs)

    return decorated


def current_user_role() -> str | None:
    """Return the Role of the current user in their tenant, or None."""
    from models import TenantUser

    user_id = session.get("user_id")
    if not user_id:
        return None
    tu = TenantUser.query.filter_by(user_id=user_id).first()
    return tu.role if tu else None


def require_role(*roles: str) -> Callable[..., Any]:
    """Decorator: abort 403 if the current user's role is not in *roles*."""

    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(f)
        def decorated(*args: Any, **kwargs: Any) -> Any:
            if current_user_role() not in roles:
                abort(403)
            return f(*args, **kwargs)

        return decorated

    return decorator


def require_pilot_access(f: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator: abort 403 unless the user has pilot access.

    Pilot access is granted by ADMIN/OWNER/PILOT/STUDENT/INSTRUCTOR role,
    or by the per-user is_pilot capability flag.
    """

    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        from models import Role, User, db

        role = current_user_role()
        if role in (Role.ADMIN, Role.OWNER, Role.PILOT, Role.STUDENT, Role.INSTRUCTOR):
            return f(*args, **kwargs)
        uid = session.get("user_id")
        if uid:
            user = db.session.get(User, uid)
            if user and user.is_pilot:
                return f(*args, **kwargs)
        return abort(403)

    return decorated


def require_maint_access(f: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator: abort 403 unless the user has maintenance access.

    Maintenance access is granted by ADMIN/OWNER/MAINTENANCE/INSTRUCTOR role,
    or by the per-user is_maintenance capability flag.
    """

    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        from models import Role, User, db

        role = current_user_role()
        if role in (Role.ADMIN, Role.OWNER, Role.MAINTENANCE, Role.INSTRUCTOR):
            return f(*args, **kwargs)
        uid = session.get("user_id")
        if uid:
            user = db.session.get(User, uid)
            if user and user.is_maintenance:
                return f(*args, **kwargs)
        return abort(403)

    return decorated


def user_can_access_aircraft(aircraft_id: int) -> bool:
    """Return True when the current user may access this aircraft.

    ADMIN and OWNER bypass the check entirely.  Other roles need either a
    UserAllAircraftAccess row (all-planes grant) or a per-aircraft
    UserAircraftAccess row.
    """
    from models import Role, TenantUser, UserAircraftAccess, UserAllAircraftAccess

    role = current_user_role()
    if role in (Role.ADMIN, Role.OWNER):
        return True
    uid = session.get("user_id")
    if not uid:
        return False
    tu = TenantUser.query.filter_by(user_id=uid).first()
    if (
        tu
        and UserAllAircraftAccess.query.filter_by(
            user_id=uid, tenant_id=tu.tenant_id
        ).first()
    ):
        return True
    return (
        UserAircraftAccess.query.filter_by(user_id=uid, aircraft_id=aircraft_id).first()
        is not None
    )


def accessible_aircraft(tenant_id: int) -> Any:
    """Return a query of Aircraft the current user is allowed to see.

    ADMIN and OWNER see every aircraft in the tenant.  A user with a
    UserAllAircraftAccess row for the tenant also sees all aircraft.
    Other roles see only aircraft granted via UserAircraftAccess.
    """
    from models import Aircraft, Role, UserAircraftAccess, UserAllAircraftAccess

    base = Aircraft.query.filter_by(tenant_id=tenant_id).order_by(Aircraft.registration)
    role = current_user_role()
    if role in (Role.ADMIN, Role.OWNER):
        return base
    uid = session.get("user_id")
    if not uid:
        from sqlalchemy import false

        return base.filter(false())
    if UserAllAircraftAccess.query.filter_by(user_id=uid, tenant_id=tenant_id).first():
        return base
    ids = [
        row.aircraft_id
        for row in (
            UserAircraftAccess.query.filter_by(user_id=uid)
            .with_entities(UserAircraftAccess.aircraft_id)
            .all()
        )
    ]
    if not ids:
        from sqlalchemy import false

        return base.filter(false())
    return base.filter(Aircraft.id.in_(ids))


def compute_aircraft_statuses(
    aircraft_list: Any, triggers: Any, hobbs_by_id: Any
) -> dict[int, str]:
    """Return {aircraft_id: 'grounded'|'overdue'|'due_soon'|'ok'} for every aircraft.

    Grounded (expired insurance or unresolved grounding snag) takes priority.
    Among maintenance: overdue > due_soon > ok.
    Insurance expiring soon maps to due_soon.
    """
    by_aircraft = defaultdict(list)
    for t in triggers:
        by_aircraft[t.aircraft_id].append(t)

    result = {}
    for ac in aircraft_list:
        if ac.is_grounded:
            result[ac.id] = "grounded"
            continue
        hobbs = hobbs_by_id.get(ac.id)
        statuses = [t.status(hobbs) for t in by_aircraft.get(ac.id, [])]
        ins = ac.insurance_status
        if ins == "expiring_soon":
            statuses.append("due_soon")
        if "overdue" in statuses:
            result[ac.id] = "overdue"
        elif "due_soon" in statuses:
            result[ac.id] = "due_soon"
        else:
            result[ac.id] = "ok"
    return result
