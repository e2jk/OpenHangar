"""A light version of a GPS track, for maps of many flights at once.

A stored track keeps up to ~500 points per part (aircraft/gps_import.py),
with altitude, at full precision — right for one flight's page, far too much
for an aircraft's or a pilot's map of every flight (and its animation and
GIF), which only needs each track's shape at map scale. The overview is that
shape: Douglas-Peucker simplified (a few tens of metres), at most
MAX_POINTS points, [lon, lat] rounded to ~1 m, no altitude. It's stored with
the track (GpsTrack.overview), refreshed whenever the track's geometry is
saved (models: before_insert/before_update), so an aircraft's or a pilot's
map is just its tracks' overviews together — nothing per aircraft or pilot
to keep in sync.
"""

from __future__ import annotations

from typing import Any

MAX_POINTS = 300
_TOLERANCE_DEG = 0.0003  # ~30 m: invisible on a map of whole flights
_DECIMALS = 5  # ~1 m


def _line(geojson: Any) -> list[list[float]]:
    """The [lon, lat] points of a stored track (a LineString Feature or
    geometry, or a MultiLineString)."""
    if not isinstance(geojson, dict):
        return []
    geom = (
        geojson.get("geometry", geojson)
        if geojson.get("type") == "Feature"
        else geojson
    )
    if not isinstance(geom, dict):
        return []
    coords = geom.get("coordinates") or []
    if geom.get("type") == "MultiLineString":
        coords = [c for seg in coords for c in (seg or [])]
    elif geom.get("type") != "LineString":
        return []
    return [
        [float(c[0]), float(c[1])]
        for c in coords
        if isinstance(c, (list, tuple)) and len(c) >= 2
    ]


def _simplify(points: list[list[float]], tolerance: float) -> list[list[float]]:
    """Douglas-Peucker, iterative (no recursion limit on long tracks)."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        (x1, y1), (x2, y2) = points[first], points[last]
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5
        worst, index = 0.0, -1
        for i in range(first + 1, last):
            px, py = points[i]
            if norm:
                dist = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / norm
            else:
                dist = ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
            if dist > worst:
                worst, index = dist, i
        if index >= 0 and worst > tolerance:
            keep[index] = True
            stack += [(first, index), (index, last)]
    return [p for p, k in zip(points, keep, strict=True) if k]


def overview_coords(geojson: Any) -> list[list[float]] | None:
    """The overview line of a stored track, or None when it has no line."""
    points = _line(geojson)
    if len(points) < 2:
        return None
    tolerance = _TOLERANCE_DEG
    simplified = _simplify(points, tolerance)
    while len(simplified) > MAX_POINTS:
        tolerance *= 2
        simplified = _simplify(points, tolerance)
    return [[round(lon, _DECIMALS), round(lat, _DECIMALS)] for lon, lat in simplified]


def overview_geojson(track: Any) -> dict[str, Any] | None:
    """A track's overview as a GeoJSON Feature (a LineString), for the maps of many
    flights (computed on the fly for a track not saved since overviews
    exist)."""
    if track is None:
        return None
    coords = (
        track.overview if track.overview is not None else overview_coords(track.geojson)
    )
    if not coords:
        return None
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {},
    }
