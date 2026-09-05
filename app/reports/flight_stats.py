"""Backlog: aggregate flight-statistics view.

Pure calculation functions, kept independent of Flask routing (mirrors the
style of reports/utilization.py) so they can be unit-tested deterministically.
Distance/geography figures are computed from app/data/airports.csv (OurAirports)
coordinates — a leg between two ICAO codes not in that dataset (or missing)
simply doesn't contribute distance/geo figures, but is still counted towards
flight_count and total_hours.
"""

from collections import defaultdict
from datetime import date as _date
from typing import Any

from models import Flight  # pyright: ignore[reportMissingImports]
from utils import (  # pyright: ignore[reportMissingImports]
    _load_airport_geo,
    haversine_nm,
)

__all__ = ["compute_flight_stats"]


def _flight_hours(f: Flight) -> float:
    """Prefer the directly-logged flight_time over the counter delta —
    mirrors reports/utilization.py's _period_stats() and the flight list
    template's duration display."""
    if f.flight_time is not None:
        return float(f.flight_time)
    if (
        f.flight_time_counter_end is not None
        and f.flight_time_counter_start is not None
    ):
        return float(f.flight_time_counter_end) - float(f.flight_time_counter_start)
    return 0.0


def _airport_info(icao: str, geo: dict[str, Any]) -> dict[str, Any]:
    row = geo[icao]
    return {
        "icao": icao,
        "name": row["name"],
        "country": row["country"],
        "region": row["region"],
    }


def compute_flight_stats(flights: list[Flight]) -> dict[str, Any]:
    geo = _load_airport_geo()

    total_hours = 0.0
    total_distance_nm = 0.0
    visited_icaos: set[str] = set()
    regions_visited: set[str] = set()
    countries_visited: set[str] = set()
    longest_leg: dict[str, Any] | None = None
    day_distance: dict[_date, float] = defaultdict(float)
    day_flight_count: dict[_date, int] = defaultdict(int)

    for f in flights:
        total_hours += _flight_hours(f)
        day_flight_count[f.date] += 1

        for icao in (f.departure_icao, f.arrival_icao):
            if icao and icao in geo:
                visited_icaos.add(icao)
                if geo[icao]["region"]:
                    regions_visited.add(geo[icao]["region"])
                if geo[icao]["country"]:
                    countries_visited.add(geo[icao]["country"])

        dep, arr = f.departure_icao, f.arrival_icao
        if dep and arr and dep in geo and arr in geo:
            distance = haversine_nm(
                geo[dep]["lat"], geo[dep]["lon"], geo[arr]["lat"], geo[arr]["lon"]
            )
            total_distance_nm += distance
            day_distance[f.date] += distance
            if longest_leg is None or distance > longest_leg["distance_nm"]:
                longest_leg = {
                    "flight_id": f.id,
                    "aircraft_id": f.aircraft_id,
                    "date": f.date,
                    "dep": dep,
                    "arr": arr,
                    "distance_nm": round(distance, 1),
                }

    airport_rows = [_airport_info(icao, geo) for icao in visited_icaos]

    def _extreme(key: str, reverse: bool) -> dict[str, Any] | None:
        if not visited_icaos:
            return None
        icao = (max if reverse else min)(visited_icaos, key=lambda i: geo[i][key])
        info = _airport_info(icao, geo)
        info[key] = geo[icao][key]
        return info

    def _extreme_elevation(reverse: bool) -> dict[str, Any] | None:
        candidates = [i for i in visited_icaos if geo[i]["elevation_ft"] is not None]
        if not candidates:
            return None
        icao = (max if reverse else min)(
            candidates, key=lambda i: geo[i]["elevation_ft"]
        )
        info = _airport_info(icao, geo)
        info["elevation_ft"] = geo[icao]["elevation_ft"]
        return info

    longest_day: dict[str, Any] | None = None
    if day_distance:
        best_date = max(day_distance, key=lambda d: day_distance[d])
        longest_day = {
            "date": best_date,
            "distance_nm": round(day_distance[best_date], 1),
            "flight_count": day_flight_count[best_date],
        }

    return {
        "flight_count": len(flights),
        "total_hours": round(total_hours, 1),
        "total_distance_nm": round(total_distance_nm, 1),
        "airports_visited": sorted(airport_rows, key=lambda r: r["icao"]),
        "airport_count": len(visited_icaos),
        "country_count": len(countries_visited),
        "region_count": len(regions_visited),
        "farthest_north": _extreme("lat", reverse=True),
        "farthest_south": _extreme("lat", reverse=False),
        "farthest_east": _extreme("lon", reverse=True),
        "farthest_west": _extreme("lon", reverse=False),
        "highest_field": _extreme_elevation(reverse=True),
        "lowest_field": _extreme_elevation(reverse=False),
        "longest_leg": longest_leg,
        "longest_day": longest_day,
    }
