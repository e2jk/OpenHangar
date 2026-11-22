"""JSON-ready map decorations from already membership-scoped link rows."""

from typing import Any

from flask import url_for


def airfield_map_data(rows: list[dict[str, Any]]) -> dict[str, Any]:
    markers: dict[tuple[Any, ...], dict[str, Any]] = {}
    lines = []
    for row in rows:
        latitude, longitude = row["latitude"], row["longitude"]
        if latitude is None or longitude is None:
            continue
        key = (row["airfield_id"], latitude, longitude, row["name"])
        marker = markers.setdefault(
            key,
            {
                "latitude": latitude,
                "longitude": longitude,
                "name": f"{row['icao_code'] or row['ident'] or ''} {row['name']}".strip(),
                "places": [],
            },
        )
        marker["places"].append(
            {
                "name": row["place_name"],
                "url": url_for("places.place_detail", place_id=row["place_id"]),
            }
        )
        if row["place_latitude"] is not None and row["place_longitude"] is not None:
            lines.append(
                {
                    "start": [row["place_latitude"], row["place_longitude"]],
                    "end": [latitude, longitude],
                }
            )
    return {"map_airfields": list(markers.values()), "map_links": lines}
