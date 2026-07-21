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
from werkzeug.routing import (  # pyright: ignore[reportMissingImports]
    BaseConverter,
    ValidationError,
)

_log = logging.getLogger(__name__)


def to_libpq_url(database_url: str) -> str:
    """Strip a SQLAlchemy dialect+driver suffix (e.g. postgresql+psycopg://)
    down to a plain postgresql:// URL, which is what libpq CLI tools
    (pg_dump, psql) expect — they don't understand the +driver suffix."""
    scheme, sep, rest = database_url.partition("://")
    return f"{scheme.split('+', 1)[0]}{sep}{rest}" if sep else database_url


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
)  # previously drawn tracks: slightly darker purple (GIF uses flat colour; web uses opacity fade)
_BG_COLOUR = (248, 248, 252)  # near-white background


def _mercator_y(lat_deg: float) -> float:
    """Web-Mercator y value for a latitude (not clamped)."""
    lat = math.radians(max(-85.0, min(85.0, lat_deg)))
    return math.log(math.tan(math.pi / 4 + lat / 2))


def _build_gif_projection(
    all_coords: list[tuple[float, float]],
    canvas_w: int = _GIF_W,
    canvas_h: int = _GIF_H,
    pad: int = _GIF_PAD,
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

    usable_w = canvas_w - 2 * pad
    usable_h = canvas_h - 2 * pad

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

    off_x = pad + (usable_w - x_range * scale_x) / 2
    off_y = pad + (usable_h - y_range * scale_y) / 2

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
    tile_cache: dict[Any, bytes] | None = None,
    max_tiles: int = 36,
) -> Any:
    """Fetch OSM raster tiles and composite them into a background PIL Image.

    Returns a PIL Image, or None on failure (caller falls back to plain fill).
    Tiles are fetched from OpenStreetMap; zoom level is chosen automatically.
    Max 36 tiles are fetched to keep export time reasonable.

    tile_cache, if provided, is a dict keyed by (z, tx, ty) or ("opi", z, tx, ty)
    holding raw PNG bytes so repeated calls across frames skip network fetches.
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
    if (tx_max - tx_min + 1) * (ty_max - ty_min + 1) > max_tiles:
        return None

    bg = _Img.new("RGB", (canvas_w, canvas_h), _BG_COLOUR)
    ua = "OpenHangar flight-logbook GIF export (https://github.com/e2jk/OpenHangar)"

    for tx in range(tx_min, tx_max + 1):
        for ty in range(ty_min, ty_max + 1):
            try:
                tx_w = tx % n
                # Compute pixel bounds for this tile by projecting its NW corner
                # and the NW corner of the tile to its SE — then size the resize
                # to exactly span that gap plus 1 px overlap, eliminating seams
                # caused by integer rounding of adjacent corner coordinates.
                lon_nw, lat_nw = _tile_nw_lonlat(tx, ty)
                lon_se, lat_se = _tile_nw_lonlat(tx + 1, ty + 1)
                px, py = project(lon_nw, lat_nw)
                px_se, py_se = project(lon_se, lat_se)
                tile_w = max(1, px_se - px + 1)
                tile_h = max(1, py_se - py + 1)

                # Base map: CARTO light (same as web animation)
                base_key = (z, tx_w, ty)
                if tile_cache is not None and base_key in tile_cache:
                    raw = tile_cache[base_key]
                else:
                    base_url = (
                        f"https://a.basemaps.cartocdn.com/light_all/{z}/{tx_w}/{ty}.png"
                    )
                    req = urllib.request.Request(base_url, headers={"User-Agent": ua})
                    with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310  # base_url has a hardcoded https:// prefix
                        raw = resp.read()
                    if tile_cache is not None:
                        tile_cache[base_key] = raw
                tile = _Img.open(io.BytesIO(raw)).convert("RGBA")
                tile = tile.resize((tile_w, tile_h), _Img.Resampling.LANCZOS)
                bg.paste(tile.convert("RGB"), (px, py))
                # Aviation overlay: OpenAIP (only at zoom ≤ 14, requires key)
                if openaip_key and z <= 14:
                    opi_key = ("opi", z, tx_w, ty)
                    if tile_cache is not None and opi_key in tile_cache:
                        opi_raw = tile_cache[opi_key]
                    else:
                        opi_url = f"https://api.tiles.openaip.net/api/data/openaip/{z}/{tx_w}/{ty}.png?apiKey={openaip_key}"
                        opi_req = urllib.request.Request(
                            opi_url, headers={"User-Agent": ua}
                        )
                        with urllib.request.urlopen(opi_req, timeout=5) as opi_resp:  # nosec B310  # opi_url has a hardcoded https:// prefix
                            opi_raw = opi_resp.read()
                        if tile_cache is not None:
                            tile_cache[opi_key] = opi_raw
                    opi_tile = _Img.open(io.BytesIO(opi_raw)).convert("RGBA")
                    opi_tile = opi_tile.resize(
                        (tile_w, tile_h), _Img.Resampling.LANCZOS
                    )
                    bg.paste(opi_tile.convert("RGB"), (px, py), opi_tile)
            except Exception as exc:
                _log.debug("OPI tile unavailable, leaving area as background: %s", exc)

    return bg


def _canvas_geo_bounds(
    project_fn: Callable[[float, float], tuple[int, int]],
    canvas_w: int,
    canvas_h: int,
    min_lon: float,
    max_lon: float,
    min_lat: float,
    max_lat: float,
) -> tuple[float, float, float, float]:
    """Return (lon_min, lat_min, lon_max, lat_max) spanning the full canvas.

    Expands the geographic bbox beyond the track bounding box so the full
    canvas area is covered by map tiles, eliminating plain-background padding.
    Returns the original bbox if the inverse projection cannot be computed.
    """
    mid_lat = (min_lat + max_lat) / 2.0
    # Longitude is linear with pixel x in Mercator
    px0, _ = project_fn(min_lon, mid_lat)
    px1, _ = project_fn(min_lon + 1.0, mid_lat)
    scale_x = float(px1 - px0)
    if scale_x <= 0:
        return min_lon, min_lat, max_lon, max_lat
    c_lon_min = min_lon - float(px0) / scale_x
    c_lon_max = min_lon + float(canvas_w - px0) / scale_x

    # Latitude: derive Mercator scale from two known projection points
    _, py_bot = project_fn(min_lon, min_lat)  # larger py (bottom of canvas area)
    _, py_top = project_fn(min_lon, max_lat)  # smaller py (top of canvas area)
    merc_bot = _mercator_y(min_lat)
    merc_top = _mercator_y(max_lat)
    merc_range = merc_top - merc_bot
    py_range = float(py_bot - py_top)
    if merc_range <= 0 or py_range <= 0:
        return c_lon_min, min_lat, c_lon_max, max_lat
    scale_y = py_range / merc_range  # pixels per Mercator-y unit
    # Derived: merc_y at canvas row py = merc_top + (py_top - py) / scale_y
    merc_canvas_top = merc_top + float(py_top) / scale_y
    merc_canvas_bot = merc_top + float(py_top - canvas_h) / scale_y
    # Clamp to avoid math.atan overflow at extreme Mercator values
    merc_canvas_top = max(-10.0, min(10.0, merc_canvas_top))
    merc_canvas_bot = max(-10.0, min(10.0, merc_canvas_bot))
    c_lat_max = math.degrees(2.0 * math.atan(math.exp(merc_canvas_top)) - math.pi / 2.0)
    c_lat_min = math.degrees(2.0 * math.atan(math.exp(merc_canvas_bot)) - math.pi / 2.0)
    return c_lon_min, max(-85.0, c_lat_min), c_lon_max, min(85.0, c_lat_max)


def sort_tracks_oldest_first(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return rows sorted ascending by their 'date' string key (oldest first).

    Both the web animation and the GIF export use this ordering so that
    chronological playback and progressive opacity fading are consistent
    across rendering environments.  Call this once in the route handler and
    pass the result to both the template context and generate_tracks_gif().
    """
    return sorted(rows, key=lambda r: r.get("date", ""))


def generate_tracks_gif(
    track_rows: list[dict[str, Any]],
    _font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    _openaip_key: str | None = None,
    canvas_w: int = _GIF_W,
    canvas_h: int = _GIF_H,
    high_res: bool = False,
) -> bytes:
    """Render an animated GIF of the flight tracks, oldest-first.

    track_rows must already be sorted oldest-first; use sort_tracks_oldest_first().
    Each frame adds one more track (cumulative) and re-fits the map to the
    bounding box of tracks seen so far, creating a progressive zoom-out effect
    that mirrors the web animation.  Previous tracks are drawn in a lighter
    colour; the newest one in vivid purple.  Returns raw GIF bytes ready to
    stream to the browser.

    high_res=True doubles line widths, font sizes and padding, uses a 256-colour
    palette, and raises the tile-fetch cap to 64 — producing a sharper map with
    readable labels at the cost of a larger file.
    """
    from PIL import Image, ImageDraw, ImageFont  # pyright: ignore[reportMissingImports]

    _q_scale = 2 if high_res else 1
    _pad = _GIF_PAD * _q_scale
    _line_curr = 3 * _q_scale
    _line_hist = 2 * _q_scale
    _font_sz = 14 * _q_scale
    _font_sz_sm = 11 * _q_scale
    _txt_margin = 8 * _q_scale
    _q_colors = 256 if high_res else 128
    _max_tiles = 64 if high_res else 36
    # Canvas-extent calls cover the full 1600×960 canvas which can require
    # ~70–90 tiles at certain zoom levels (wide flat tracks at z=7), so use a
    # higher cap than the track-bbox call.  128 = 2× the high-res track cap.
    _max_tiles_canvas = _max_tiles * 2 if high_res else _max_tiles

    # Pre-compute per-track coordinates (avoids re-parsing GeoJSON in the inner loop)
    per_track_coords = [_coords_from_geojson(r.get("geojson")) for r in track_rows]
    all_coords: list[tuple[float, float]] = [c for tc in per_track_coords for c in tc]

    if len(all_coords) < 2:
        # Fallback: single blank frame
        img = Image.new("RGB", (canvas_w, canvas_h), _BG_COLOUR)
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        return buf.getvalue()

    font: Any  # PIL font type varies across Pillow versions
    font_sm: Any
    try:
        font = ImageFont.truetype(_font_path, _font_sz)
        font_sm = ImageFont.truetype(_font_path, _font_sz_sm)
    except (IOError, OSError):
        font = font_sm = ImageFont.load_default()

    def draw_shadow_text(draw: Any, text: str, font: Any) -> None:
        draw.text(
            (_txt_margin + 1, _txt_margin + 1), text, fill=(255, 255, 255), font=font
        )
        draw.text((_txt_margin, _txt_margin), text, fill=(40, 40, 60), font=font)

    def draw_track(
        draw: Any,
        coords: list[tuple[float, float]],
        colour: tuple[int, int, int],
        width: int,
        project_fn: Callable[[float, float], tuple[int, int]],
    ) -> None:
        pts = [project_fn(lon, lat) for lon, lat in coords]
        if len(pts) >= 2:
            draw.line(pts, fill=colour, width=width)
            r = width + 2 * _q_scale
            sx, sy = pts[0]
            draw.ellipse([sx - r, sy - r, sx + r, sy + r], fill=(40, 160, 60))
            ex, ey = pts[-1]
            draw.ellipse([ex - r, ey - r, ex + r, ey + r], fill=(200, 40, 40))

    # Shared tile cache: (z, tx, ty) → raw PNG bytes; ("opi", z, tx, ty) for OpenAIP.
    # Avoids re-fetching tiles that appear in multiple frames at the same zoom level.
    tile_cache: dict[Any, bytes] = {}

    def _frame_bg(
        project_fn: Callable[[float, float], tuple[int, int]],
        min_lon: float,
        max_lon: float,
        min_lat: float,
        max_lat: float,
    ) -> Any:
        fetch_lon_min, fetch_lat_min, fetch_lon_max, fetch_lat_max = (
            min_lon,
            min_lat,
            max_lon,
            max_lat,
        )
        if high_res:
            fetch_lon_min, fetch_lat_min, fetch_lon_max, fetch_lat_max = (
                _canvas_geo_bounds(
                    project_fn,
                    canvas_w,
                    canvas_h,
                    min_lon,
                    max_lon,
                    min_lat,
                    max_lat,
                )
            )
        bg = _make_tile_background(
            project_fn,
            fetch_lon_min,
            fetch_lon_max,
            fetch_lat_min,
            fetch_lat_max,
            canvas_w,
            canvas_h,
            openaip_key=_openaip_key,
            tile_cache=tile_cache,
            max_tiles=_max_tiles_canvas,
        )
        if bg is None and high_res:
            # Canvas extent still exceeded the raised cap; fall back to track bbox
            bg = _make_tile_background(
                project_fn,
                min_lon,
                max_lon,
                min_lat,
                max_lat,
                canvas_w,
                canvas_h,
                openaip_key=_openaip_key,
                tile_cache=tile_cache,
                max_tiles=_max_tiles,
            )
        return (
            bg.copy()
            if bg is not None
            else Image.new("RGB", (canvas_w, canvas_h), _BG_COLOUR)
        )

    frames: list[Any] = []
    durations: list[int] = []
    accumulated_coords: list[tuple[float, float]] = []

    for frame_idx in range(len(track_rows)):
        accumulated_coords.extend(per_track_coords[frame_idx])
        proj_result = _build_gif_projection(
            accumulated_coords, canvas_w=canvas_w, canvas_h=canvas_h, pad=_pad
        )
        if proj_result is None:
            continue  # not enough coords yet (e.g. leading rows with no geojson)

        project_fn, (f_min_lon, f_min_lat, f_max_lon, f_max_lat) = proj_result
        img = _frame_bg(project_fn, f_min_lon, f_max_lon, f_min_lat, f_max_lat)
        draw = ImageDraw.Draw(img)

        for i in range(frame_idx):
            draw_track(
                draw, per_track_coords[i], _HISTORY_COLOUR, _line_hist, project_fn
            )
        draw_track(
            draw, per_track_coords[frame_idx], _TRACK_COLOUR, _line_curr, project_fn
        )

        row = track_rows[frame_idx]
        label = f"{row.get('date', '')}  {row.get('dep', '')} → {row.get('arr', '')}"
        draw_shadow_text(draw, label, font)
        draw.text(
            (_txt_margin, canvas_h - _font_sz_sm - _txt_margin),
            f"{frame_idx + 1} / {len(track_rows)}",
            fill=(80, 80, 100),
            font=font_sm,
        )

        frames.append(img)
        durations.append(_GIF_STEP_MS)

    # Final frame: all tracks at equal weight using the full bounding box, longer hold
    if frames:
        proj_result_final = _build_gif_projection(
            all_coords, canvas_w=canvas_w, canvas_h=canvas_h, pad=_pad
        )
        # proj_result_final is guaranteed non-None: all_coords has ≥ 2 points (checked above)
        project_final, (f_min_lon, f_min_lat, f_max_lon, f_max_lat) = proj_result_final  # type: ignore[misc]
        img = _frame_bg(project_final, f_min_lon, f_max_lon, f_min_lat, f_max_lat)
        draw = ImageDraw.Draw(img)
        for tc in per_track_coords:
            draw_track(draw, tc, _TRACK_COLOUR, _line_hist, project_final)
        draw_shadow_text(draw, f"All {len(track_rows)} tracks", font)
        frames.append(img)
        durations.append(_GIF_HOLD_MS)

    # Quantise all frames to a shared 128-colour palette built from the last frame
    # (widest view, most visually representative).  A single global palette means
    # identical background areas compress very well with GIF's LZW codec.
    palette_src = frames[-1].quantize(colors=_q_colors, dither=Image.Dither.NONE)
    quantized: list[Any] = [
        f.quantize(palette=palette_src, dither=Image.Dither.NONE) for f in frames
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


def generate_single_track_image(
    geojson: dict[str, Any] | None,
    date: str = "",
    dep: str = "",
    arr: str = "",
    _font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    _openaip_key: str | None = None,
    canvas_w: int = _GIF_W,
    canvas_h: int = _GIF_H,
    high_res: bool = False,
) -> bytes:
    """Render a single GPS track as a static PNG.

    Returns raw PNG bytes.  If the track has fewer than 2 points a blank
    canvas is returned so the caller can always stream a valid image.
    """
    from PIL import Image, ImageDraw, ImageFont  # pyright: ignore[reportMissingImports]

    all_coords = _coords_from_geojson(geojson)

    _q_scale = 2 if high_res else 1
    _pad = _GIF_PAD * _q_scale
    _line_w = 3 * _q_scale
    _font_sz = 14 * _q_scale
    _txt_margin = 8 * _q_scale
    _max_tiles = 64 if high_res else 36
    _max_tiles_canvas = _max_tiles * 2 if high_res else _max_tiles

    def _blank_png() -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (canvas_w, canvas_h), _BG_COLOUR).save(buf, format="PNG")
        return buf.getvalue()

    if len(all_coords) < 2:
        return _blank_png()

    proj_result = _build_gif_projection(
        all_coords, canvas_w=canvas_w, canvas_h=canvas_h, pad=_pad
    )
    if proj_result is None:
        return _blank_png()

    project_fn, (f_min_lon, f_min_lat, f_max_lon, f_max_lat) = proj_result

    tile_cache: dict[Any, bytes] = {}
    fetch_lon_min, fetch_lat_min, fetch_lon_max, fetch_lat_max = (
        f_min_lon,
        f_min_lat,
        f_max_lon,
        f_max_lat,
    )
    if high_res:
        fetch_lon_min, fetch_lat_min, fetch_lon_max, fetch_lat_max = _canvas_geo_bounds(
            project_fn, canvas_w, canvas_h, f_min_lon, f_max_lon, f_min_lat, f_max_lat
        )
    bg = _make_tile_background(
        project_fn,
        fetch_lon_min,
        fetch_lon_max,
        fetch_lat_min,
        fetch_lat_max,
        canvas_w,
        canvas_h,
        openaip_key=_openaip_key,
        tile_cache=tile_cache,
        max_tiles=_max_tiles_canvas,
    )
    if bg is None and high_res:
        bg = _make_tile_background(
            project_fn,
            f_min_lon,
            f_max_lon,
            f_min_lat,
            f_max_lat,
            canvas_w,
            canvas_h,
            openaip_key=_openaip_key,
            tile_cache=tile_cache,
            max_tiles=_max_tiles,
        )
    img = (
        bg.copy()
        if bg is not None
        else Image.new("RGB", (canvas_w, canvas_h), _BG_COLOUR)
    )

    draw = ImageDraw.Draw(img)
    pts = [project_fn(lon, lat) for lon, lat in all_coords]
    if len(pts) >= 2:
        draw.line(pts, fill=_TRACK_COLOUR, width=_line_w)
        r = _line_w + 2 * _q_scale
        sx, sy = pts[0]
        draw.ellipse([sx - r, sy - r, sx + r, sy + r], fill=(40, 160, 60))
        ex, ey = pts[-1]
        draw.ellipse([ex - r, ey - r, ex + r, ey + r], fill=(200, 40, 40))

    label = f"{date}  {dep} → {arr}".strip("  →").strip()
    if label:
        try:
            font: Any = ImageFont.truetype(_font_path, _font_sz)
        except (IOError, OSError):
            font = ImageFont.load_default()
        draw.text(
            (_txt_margin + 1, _txt_margin + 1), label, fill=(255, 255, 255), font=font
        )
        draw.text((_txt_margin, _txt_margin), label, fill=(40, 40, 60), font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_single_track_gif(
    geojson: dict[str, Any] | None,
    date: str = "",
    dep: str = "",
    arr: str = "",
    _font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    _openaip_key: str | None = None,
    canvas_w: int = _GIF_W,
    canvas_h: int = _GIF_H,
    high_res: bool = False,
) -> bytes:
    """Render an animated GIF of a single flight track drawn progressively.

    The track's coordinate array is split into N equal chunks (3–10 depending
    on track length).  Each frame draws all chunks revealed so far, fitting the
    map bounding box to the accumulated coords so the view zooms out as the
    route grows — mirroring the per-flight behaviour of the web Animate button.

    Falls back to a single-frame GIF wrapping the still PNG when the track has
    fewer than 20 GPS points (too sparse for meaningful animation).
    """
    from PIL import Image, ImageDraw, ImageFont  # pyright: ignore[reportMissingImports]

    all_coords = _coords_from_geojson(geojson)

    _q_scale = 2 if high_res else 1
    _pad = _GIF_PAD * _q_scale
    _line_curr = 3 * _q_scale
    _line_hist = 2 * _q_scale
    _font_sz = 14 * _q_scale
    _font_sz_sm = 11 * _q_scale
    _txt_margin = 8 * _q_scale
    _q_colors = 256 if high_res else 128
    _max_tiles = 64 if high_res else 36
    _max_tiles_canvas = _max_tiles * 2 if high_res else _max_tiles

    def _blank_gif() -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (canvas_w, canvas_h), _BG_COLOUR).save(buf, format="GIF")
        return buf.getvalue()

    if len(all_coords) < 2:
        return _blank_gif()

    # Sparse-track fallback: wrap the still image in a single-frame GIF
    if len(all_coords) < 20:
        png = generate_single_track_image(
            geojson,
            date=date,
            dep=dep,
            arr=arr,
            _font_path=_font_path,
            _openaip_key=_openaip_key,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            high_res=high_res,
        )
        img = Image.open(io.BytesIO(png)).convert("RGB")
        buf = io.BytesIO()
        img.quantize(colors=_q_colors, dither=Image.Dither.NONE).save(buf, format="GIF")
        return buf.getvalue()

    # Split coords into N equal chunks (3–10)
    n_chunks = max(3, min(10, len(all_coords) // 10))
    chunk_size = len(all_coords) // n_chunks
    chunks: list[list[tuple[float, float]]] = [
        list(all_coords[i * chunk_size : (i + 1) * chunk_size])
        for i in range(n_chunks - 1)
    ]
    chunks.append(
        list(all_coords[(n_chunks - 1) * chunk_size :])
    )  # last catches remainder

    try:
        font: Any = ImageFont.truetype(_font_path, _font_sz)
        font_sm: Any = ImageFont.truetype(_font_path, _font_sz_sm)
    except (IOError, OSError):
        font = font_sm = ImageFont.load_default()

    def draw_shadow_text(draw: Any, text: str, fnt: Any) -> None:
        draw.text(
            (_txt_margin + 1, _txt_margin + 1), text, fill=(255, 255, 255), font=fnt
        )
        draw.text((_txt_margin, _txt_margin), text, fill=(40, 40, 60), font=fnt)

    def draw_track(
        draw: Any,
        coords: list[tuple[float, float]],
        colour: tuple[int, int, int],
        width: int,
        project_fn: Callable[[float, float], tuple[int, int]],
    ) -> None:
        pts = [project_fn(lon, lat) for lon, lat in coords]
        if len(pts) >= 2:
            draw.line(pts, fill=colour, width=width)
            r = width + 2 * _q_scale
            sx, sy = pts[0]
            draw.ellipse([sx - r, sy - r, sx + r, sy + r], fill=(40, 160, 60))
            ex, ey = pts[-1]
            draw.ellipse([ex - r, ey - r, ex + r, ey + r], fill=(200, 40, 40))

    tile_cache: dict[Any, bytes] = {}

    def _frame_bg(
        project_fn: Callable[[float, float], tuple[int, int]],
        min_lon: float,
        max_lon: float,
        min_lat: float,
        max_lat: float,
    ) -> Any:
        fetch_lon_min, fetch_lat_min, fetch_lon_max, fetch_lat_max = (
            min_lon,
            min_lat,
            max_lon,
            max_lat,
        )
        if high_res:
            fetch_lon_min, fetch_lat_min, fetch_lon_max, fetch_lat_max = (
                _canvas_geo_bounds(
                    project_fn, canvas_w, canvas_h, min_lon, max_lon, min_lat, max_lat
                )
            )
        bg = _make_tile_background(
            project_fn,
            fetch_lon_min,
            fetch_lon_max,
            fetch_lat_min,
            fetch_lat_max,
            canvas_w,
            canvas_h,
            openaip_key=_openaip_key,
            tile_cache=tile_cache,
            max_tiles=_max_tiles_canvas,
        )
        if bg is None and high_res:
            bg = _make_tile_background(
                project_fn,
                min_lon,
                max_lon,
                min_lat,
                max_lat,
                canvas_w,
                canvas_h,
                openaip_key=_openaip_key,
                tile_cache=tile_cache,
                max_tiles=_max_tiles,
            )
        return (
            bg.copy()
            if bg is not None
            else Image.new("RGB", (canvas_w, canvas_h), _BG_COLOUR)
        )

    label = f"{date}  {dep} → {arr}".strip("  →").strip()

    frames: list[Any] = []
    durations: list[int] = []
    accumulated: list[tuple[float, float]] = []

    for chunk_idx, chunk in enumerate(chunks):
        accumulated.extend(chunk)
        proj_result = _build_gif_projection(
            accumulated, canvas_w=canvas_w, canvas_h=canvas_h, pad=_pad
        )
        if proj_result is None:
            continue

        project_fn, (f_min_lon, f_min_lat, f_max_lon, f_max_lat) = proj_result
        img = _frame_bg(project_fn, f_min_lon, f_max_lon, f_min_lat, f_max_lat)
        draw = ImageDraw.Draw(img)

        for i in range(chunk_idx):
            draw_track(draw, chunks[i], _HISTORY_COLOUR, _line_hist, project_fn)
        draw_track(draw, chunk, _TRACK_COLOUR, _line_curr, project_fn)

        if label:
            draw_shadow_text(draw, label, font)
        draw.text(
            (_txt_margin, canvas_h - _font_sz_sm - _txt_margin),
            f"{chunk_idx + 1} / {n_chunks}",
            fill=(80, 80, 100),
            font=font_sm,
        )
        frames.append(img)
        durations.append(_GIF_STEP_MS)

    if not frames:
        return _blank_gif()

    # Final hold frame: full track, all chunks at equal weight
    proj_result_final = _build_gif_projection(
        list(all_coords), canvas_w=canvas_w, canvas_h=canvas_h, pad=_pad
    )
    # guaranteed non-None: all_coords has ≥ 20 points
    project_final, (f_min_lon, f_min_lat, f_max_lon, f_max_lat) = proj_result_final  # type: ignore[misc]
    img = _frame_bg(project_final, f_min_lon, f_max_lon, f_min_lat, f_max_lat)
    draw = ImageDraw.Draw(img)
    for chunk in chunks:
        draw_track(draw, chunk, _TRACK_COLOUR, _line_hist, project_final)
    if label:
        draw_shadow_text(draw, label, font)
    frames.append(img)
    durations.append(_GIF_HOLD_MS)

    palette_src = frames[-1].quantize(colors=_q_colors, dither=Image.Dither.NONE)
    quantized: list[Any] = [
        f.quantize(palette=palette_src, dither=Image.Dither.NONE) for f in frames
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


def _load_aircraft_type_variants() -> list[tuple[str, str, str, str]]:
    """Return all (type_designator, full_name, manufacturer, model) tuples — one per CSV row.

    Unlike _load_aircraft_types(), duplicate designators are preserved so
    the search endpoint can surface every variant (e.g. all PA-28-181 models
    that share the P28A ICAO code). manufacturer and model are kept separate
    so callers can pre-fill form fields for the exact selected variant rather
    than an arbitrary one sharing the same ICAO code.
    """
    path = os.path.join(os.path.dirname(__file__), "data", "aircraft_types.csv")
    result: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                des = row.get("type_designator", "").strip().upper()
                mfr = row.get("manufacturer", "").strip()
                model = row.get("model", "").strip()
                name = f"{mfr} {model}".strip()
                if des and (des, name) not in seen:
                    result.append((des, name, mfr, model))
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


@functools.lru_cache(maxsize=1)
def _build_model_name_prefix_lookup() -> dict[str, str]:
    """Return {compact_first_word_of_model: icao_code} for resolve_aircraft_type_icao fallback.

    Enables matching pilot-logbook type strings like "DR401" or "PA28-161 TDI"
    against ICAO codes like "DR40" or "P28A" via the CSV model-name column.
    """
    path = os.path.join(os.path.dirname(__file__), "data", "aircraft_types.csv")
    result: dict[str, str] = {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                des = row.get("type_designator", "").strip().upper()
                model = row.get("model", "").strip()
                if not des or not model:
                    continue
                first_word = model.split()[0]
                key = first_word.replace("-", "").replace(" ", "").upper()
                if key and key not in result:
                    result[key] = des
    except OSError as exc:
        _log.warning("aircraft_types.csv not found: %s", exc)
    return result


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
    # Try matching against the first word of each CSV model name (longest key wins).
    # e.g. "DR401" startswith "DR401" (from "DR-401 135CDI") → DR40
    #      "PA28161TDI" startswith "PA28161" (from "PA-28-161 Cherokee…") → P28A
    # Guard: reject if the unmatched tail starts with a digit — that signals a
    # different model number (e.g. "C172RG" wrongly matching key "C17").
    prefix_lookup = _build_model_name_prefix_lookup()
    for key in sorted(prefix_lookup, key=len, reverse=True):
        if len(key) < 4:
            break  # keys are sorted longest-first; stop once they're too short
        if compact.startswith(key):
            tail = compact[len(key) :]
            if tail and tail[0].isdigit():
                continue  # "C172RG" matching "C17" — tail "2RG" starts with digit
            return prefix_lookup[key]
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


class AircraftRefConverter(BaseConverter):
    """URL converter for the ``aircraft_id`` slot: accepts either the numeric
    primary key (unchanged, always works) or the aircraft's registration
    (e.g. ``OO-GRN``), so routes like ``/aircraft/<aircraft_id>/flights``
    also resolve ``/aircraft/OO-GRN/flights``.

    Authorization is unaffected: this only resolves the URL segment to a
    primary key (or the reverse, for link generation) — every view still
    does its own tenant-scoped lookup exactly as before, so a registration
    belonging to another tenant 404s the same way a wrong numeric id does.

    Registrations containing '/' or spaces (rare, but not forbidden by the
    model) are sanitized the same way upload filenames already are
    elsewhere in this blueprint; such an aircraft simply isn't reachable via
    its pretty URL (only via the numeric id, which always works).
    """

    regex = r"[^/]+"

    def to_python(self, value: str) -> int:
        if value.isdigit():
            return int(value)

        from models import Aircraft, db

        needle = value.upper()
        ac = (
            Aircraft.query.filter(
                db.func.upper(
                    db.func.replace(
                        db.func.replace(Aircraft.registration, "/", "-"), " ", "-"
                    )
                )
                == needle
            )
            .order_by(Aircraft.id)
            .first()
        )
        if ac is None:
            raise ValidationError()
        return int(ac.id)

    def to_url(self, value: Any) -> str:
        if isinstance(value, str) and not value.isdigit():
            return super().to_url(value)

        from models import Aircraft, db

        ac = db.session.get(Aircraft, int(value))
        reg = ac.registration if ac and ac.registration else str(value)
        safe_reg = reg.replace("/", "-").replace(" ", "-")
        return super().to_url(safe_reg)


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


def check_update_available() -> bool:
    """Return True when a newer release is available than the running instance.

    Reads the ``update_available`` AppSetting written by the background
    version-check service (computed once per check cycle, not per request).
    Falls back to a live comparison against ``latest_version`` if the flag has
    not been written yet (fresh install before the first background check).
    Returns False on any error.  Must be called inside a request (or
    application) context.
    """
    try:
        from models import AppSetting, db  # pyright: ignore[reportMissingImports]

        flag = db.session.get(AppSetting, "update_available")
        if flag is not None:
            return bool(flag.value == "true")

        # Fallback for fresh installs: compute from latest_version directly.
        from packaging.version import Version  # pyright: ignore[reportMissingImports]

        current = os.environ.get("OPENHANGAR_VERSION", "development")
        if current == "development":
            return False
        latest_s = db.session.get(AppSetting, "latest_version")
        latest = latest_s.value if latest_s else None
        return bool(latest and Version(latest) > Version(current))
    except Exception:
        return False


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


def accessible_aircraft(tenant_id: int, include_archived: bool = False) -> Any:
    """Return a query of Aircraft the current user is allowed to see.

    ADMIN and OWNER see every aircraft in the tenant.  A user with a
    UserAllAircraftAccess row for the tenant also sees all aircraft.
    Other roles see only aircraft granted via UserAircraftAccess.

    Archived aircraft are excluded unless include_archived is True — pass it
    for views that must keep showing an archived aircraft's history.
    """
    from models import Aircraft, Role, UserAircraftAccess, UserAllAircraftAccess

    base = Aircraft.query.filter_by(tenant_id=tenant_id).order_by(Aircraft.registration)
    if not include_archived:
        base = base.filter(Aircraft.archived_at.is_(None))
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
    Among maintenance: overdue > due_soon > ok — maintenance triggers,
    component TBO / calendar life limits, and expiring insurance all count.
    """
    from services.component_limits import fleet_limit_statuses

    by_aircraft = defaultdict(list)
    for t in triggers:
        by_aircraft[t.aircraft_id].append(t)
    limit_status_by_ac = fleet_limit_statuses(aircraft_list)

    result = {}
    for ac in aircraft_list:
        if ac.is_grounded:
            result[ac.id] = "grounded"
            continue
        hobbs = hobbs_by_id.get(ac.id)
        statuses = [t.status(hobbs) for t in by_aircraft.get(ac.id, [])]
        statuses.append(limit_status_by_ac.get(ac.id, "ok"))
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
