"""Reusable allowlisted GPX/KML documents for authorised destination rows."""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from html import escape
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from flask_babel import gettext as _

FORMATS = {
    "gpx": "application/gpx+xml",
    "kml": "application/vnd.google-earth.kml+xml",
}


@dataclass(frozen=True)
class Waypoint:
    name: str
    code: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class Selection:
    points: list[Waypoint]
    omitted: list[str]


def _text(value: str) -> str:
    """Keep XML 1.0 text valid even for names containing control characters."""
    return "".join(
        char
        for char in value
        if char in "\t\n\r"
        or 0x20 <= ord(char) <= 0xD7FF
        or 0xE000 <= ord(char) <= 0xFFFD
        or 0x10000 <= ord(char) <= 0x10FFFF
    )


def _coordinates(row: dict[str, Any]) -> tuple[float, float] | None:
    try:
        lat, lon = float(row["latitude"]), float(row["longitude"])
    except (TypeError, ValueError):
        return None
    if (
        not math.isfinite(lat)
        or not math.isfinite(lon)
        or not (-90 <= lat <= 90 and -180 <= lon <= 180)
    ):
        return None
    return lat, lon


def select(
    rows: Iterable[dict[str, Any]],
) -> Selection:
    """Accept pre-authorised destination rows; retain only waypoint fields."""
    points, omitted = [], []
    for row in rows:
        name, code = _text(row["name"]), _text(row.get("ident") or "")
        coordinates = _coordinates(row)
        if coordinates is None:
            omitted.append(f"{code} — {name}" if code else name)
        else:
            points.append(Waypoint(name, code, *coordinates))
    return Selection(points, omitted)


def document(selection: Selection, fmt: str, *, label: str) -> bytes:
    """Emit GPX 1.1 / KML 2.2 with namespace and schema element ordering."""
    omitted = (
        _(
            "Omitted without usable coordinates: %(fields)s.",
            fields="; ".join(selection.omitted),
        )
        if selection.omitted
        else ""
    )
    if fmt == "gpx":
        root = Element(
            "gpx",
            {
                "xmlns": "http://www.topografix.com/GPX/1/1",
                "version": "1.1",
                "creator": "OpenHangar",
            },
        )
        metadata = SubElement(root, "metadata")
        SubElement(metadata, "name").text = label
        if omitted:
            SubElement(metadata, "desc").text = omitted
        for point in selection.points:
            lon = f"{point.longitude:.8f}"
            # GPX's longitude upper bound is exclusive; the same meridian is -180.
            if lon == "180.00000000":
                lon = "-180.00000000"
            node = SubElement(root, "wpt", lat=f"{point.latitude:.8f}", lon=lon)
            SubElement(node, "name").text = point.code or point.name
            SubElement(node, "desc").text = point.name
    elif fmt == "kml":
        root = Element("kml", {"xmlns": "http://www.opengis.net/kml/2.2"})
        container = SubElement(root, "Document")
        SubElement(container, "name").text = label
        if omitted:
            SubElement(container, "description").text = escape(omitted)
        for point in selection.points:
            node = SubElement(container, "Placemark")
            SubElement(node, "name").text = point.code or point.name
            # KML descriptions support HTML; escape it before XML serialization.
            SubElement(node, "description").text = escape(point.name)
            location = SubElement(node, "Point")
            SubElement(
                location, "coordinates"
            ).text = f"{point.longitude:.8f},{point.latitude:.8f}"
    else:
        raise ValueError("Unsupported waypoint format")
    return bytes(tostring(root, encoding="utf-8", xml_declaration=True))
