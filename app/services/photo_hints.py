"""Suggestions read from a photo before it is cleaned (Phase 46 and the photo
metadata backlog item): when it was taken, and the airfield it was taken at.

Nothing here is stored: the photo is read in memory and the caller offers
the values as suggestions the user accepts or ignores. Photos are cleaned of
all their metadata when saved (services.images).

- Time: EXIF DateTimeOriginal (else DateTime). With the camera's own
  OffsetTimeOriginal, the UTC time is exact; without it, the device's UTC
  offset sent by the browser converts it, marked as approximate; without
  either, no time. A photo without EXIF falls back to a time in its file
  name (IMG_20260512_143005, PXL_20260512_143005123, 2026-05-12 14.30.05…),
  read as device time.
- Airfield: the catalogue airfield nearest the photo's GPS position, within
  2 km (a photo taken on the apron); none when nothing is that close, or
  when a second field is about as close.
"""

from __future__ import annotations

import io
import re
import warnings
from datetime import datetime, timedelta
from typing import Any

from PIL import Image, UnidentifiedImageError
from utils import _load_airport_geo, haversine_nm

from services.images import MAX_PHOTO_BYTES, MAX_PHOTO_PIXELS

MAX_AIRFIELD_KM = 2.0
# A second field closer than this many times the nearest's distance makes
# the position ambiguous.
AMBIGUOUS_RATIO = 1.5

_EXIF_IFD = 0x8769
_GPS_IFD = 0x8825
_DATETIME = 306
_DATETIME_ORIGINAL = 36867
_OFFSET_TIME_ORIGINAL = 36881

_FILENAME_TIMES = [
    # IMG_20260512_143005, PXL_20260512_143005123, VID_…, 20260512_143005
    re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})"),
    # 2026-05-12 14.30.05, 2026-05-12_14-30-05, 2026-05-12 at 14.30.05
    re.compile(
        r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?:[ _T]|\sat\s)(\d{2})[.:-](\d{2})[.:-](\d{2})"
    ),
]


def _offset(value: Any) -> timedelta | None:
    match = re.fullmatch(r"\s*([+-])(\d{2}):?(\d{2})\s*", str(value or ""))
    if not match:
        return None
    sign, hours, minutes = match.groups()
    delta = timedelta(hours=int(hours), minutes=int(minutes))
    if delta > timedelta(hours=14):
        return None
    return delta if sign == "+" else -delta


def _exif_time(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value).strip()[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def _filename_time(name: str) -> datetime | None:
    for pattern in _FILENAME_TIMES:
        match = pattern.search(name or "")
        if match:
            try:
                year, month, day, hour, minute, second = (
                    int(part) for part in match.groups()
                )
                return datetime(year, month, day, hour, minute, second)
            except ValueError:
                continue
    return None


def _degrees(value: Any, ref: Any) -> float | None:
    try:
        degrees, minutes, seconds = (float(part) for part in value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    result = degrees + minutes / 60 + seconds / 3600
    return -result if str(ref).upper() in {"S", "W"} else result


def _airfield(latitude: float, longitude: float) -> dict[str, Any] | None:
    # A box of about ±10 km first, then the exact distances.
    distances = sorted(
        (haversine_nm(latitude, longitude, field["lat"], field["lon"]) * 1.852, ident)
        for ident, field in _load_airport_geo().items()
        if abs(field["lat"] - latitude) < 0.1 and abs(field["lon"] - longitude) < 0.2
    )
    if not distances or distances[0][0] > MAX_AIRFIELD_KM:
        return None
    nearest_km, ident = distances[0]
    if len(distances) > 1 and distances[1][0] < max(nearest_km, 0.2) * AMBIGUOUS_RATIO:
        return None
    return {
        "ident": ident,
        "name": _load_airport_geo()[ident]["name"],
        "distance_km": round(nearest_km, 1),
    }


def read_hints(
    data: bytes, filename: str, device_offset_minutes: int | None
) -> dict[str, Any]:
    """{"time", "date", "time_source", "airfield", "position"}, each None when
    unknown; `position` is {"latitude", "longitude"} from the photo's GPS.
    `time` is "HH:MM" UTC; `time_source` is "camera" (exact), "device"
    (converted with the device's offset) or "filename" (likewise)."""
    hints: dict[str, Any] = {
        "time": None,
        "date": None,
        "time_source": None,
        "airfield": None,
        "position": None,
    }
    if len(data) > MAX_PHOTO_BYTES:
        return hints
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                if img.width * img.height > MAX_PHOTO_PIXELS:
                    return hints
                exif = img.getexif()
                details = exif.get_ifd(_EXIF_IFD)
                gps = exif.get_ifd(_GPS_IFD)
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        return hints

    local = _exif_time(details.get(_DATETIME_ORIGINAL) or exif.get(_DATETIME))
    offset = _offset(details.get(_OFFSET_TIME_ORIGINAL)) if local else None
    source = "camera" if offset is not None else "device"
    if local is None:
        local = _filename_time(filename)
        source = "filename"
    if local is not None:
        if offset is None and device_offset_minutes is not None:
            offset = timedelta(minutes=device_offset_minutes)
        if offset is not None:
            utc = local - offset
            hints.update(
                time=utc.strftime("%H:%M"),
                date=utc.date().isoformat(),
                time_source=source,
            )

    latitude = _degrees(gps.get(2), gps.get(1))
    longitude = _degrees(gps.get(4), gps.get(3))
    if (
        latitude is not None
        and longitude is not None
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    ):
        hints["airfield"] = _airfield(latitude, longitude)
        hints["position"] = {
            "latitude": round(latitude, 6),
            "longitude": round(longitude, 6),
        }
    return hints
