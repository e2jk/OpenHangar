"""Parse explicit coordinates without following links or guessing place names."""

import math
import re
from urllib.parse import parse_qs, urlsplit

from flask_babel import gettext as _

from places.access import PlaceError

_NUMBER = r"[+-]?\d{1,3}(?:\.\d+)?"
_PAIR = re.compile(rf"^\s*({_NUMBER})\s*[,;\s]\s*({_NUMBER})\s*$")
_DMS = re.compile(
    r"(?P<prefix>[NSWE])?\s*(\d{1,3})\s*[°d]\s*"
    r"(?:(\d{1,2}(?:\.\d+)?)\s*['′m]\s*)?"
    r"(?:(\d{1,2}(?:\.\d+)?)\s*[\"″s]\s*)?"
    r"(?(prefix)(?:\s*(?P=prefix))?|\s*([NSWE]))",
    re.IGNORECASE,
)


def coordinate_pair(latitude: str, longitude: str) -> tuple[float, float]:
    """Form coordinates may be decimal; non-finite and out-of-range values fail."""
    try:
        if len(latitude) > 32 or len(longitude) > 32:
            raise ValueError
        lat, lon = float(latitude), float(longitude)
        if not (
            math.isfinite(lat)
            and math.isfinite(lon)
            and -90 <= lat <= 90
            and -180 <= lon <= 180
        ):
            raise ValueError
        return lat, lon
    except ValueError:
        raise PlaceError(_("Enter valid latitude and longitude coordinates.")) from None


def parse_coordinates(value: str) -> tuple[float, float]:
    """Decimal pair, hemisphere-labelled DMS, or a map URL with explicit coordinates.

    Short map links cannot be decoded offline and are deliberately not fetched.
    A parsed pin is always shown for the user to check before saving a place.
    """
    value = value.strip()
    if len(value) <= 2048:
        pair = _PAIR.fullmatch(value)
        if pair:
            return coordinate_pair(pair[1], pair[2])
        dms = list(_DMS.finditer(value))
        if len(dms) == 2 and not _DMS.sub("", value).strip(" ,;"):
            result: dict[str, float] = {}
            for match in dms:
                first, degrees, minutes, seconds, last = match.groups()
                hemisphere = (first or last or "").upper()
                mins, secs = float(minutes or 0), float(seconds or 0)
                if mins >= 60 or secs >= 60:
                    break
                key = "lat" if hemisphere in "NS" else "lon"
                result[key] = (float(degrees) + mins / 60 + secs / 3600) * (
                    -1 if hemisphere in "SW" else 1
                )
            if result.keys() == {"lat", "lon"}:
                return coordinate_pair(str(result["lat"]), str(result["lon"]))
        try:
            url = urlsplit(value)
            if url.scheme in {"http", "https"} and url.hostname:
                query = parse_qs(url.query, max_num_fields=30)
                if "mlat" in query and "mlon" in query:
                    return coordinate_pair(query["mlat"][0], query["mlon"][0])
                keys = ["q", "query"]
                # Other providers use ll=longitude,latitude. Only interpret ll
                # for known providers whose format is latitude,longitude.
                if url.hostname == "maps.apple.com" or re.fullmatch(
                    r"(?:www\.|maps\.)?google\.[a-z.]+", url.hostname
                ):
                    keys.append("ll")
                for key in keys:
                    pair = _PAIR.fullmatch(query.get(key, [""])[0])
                    if pair:
                        return coordinate_pair(pair[1], pair[2])
                pair = re.search(rf"@({_NUMBER}),({_NUMBER})(?:,|/|$)", url.path)
                if pair:
                    return coordinate_pair(pair[1], pair[2])
                pair = re.fullmatch(
                    rf"map=\d+(?:\.\d+)?/({_NUMBER})/({_NUMBER})", url.fragment
                )
                if pair:
                    return coordinate_pair(pair[1], pair[2])
                pair = re.search(rf"!3d({_NUMBER})!4d({_NUMBER})(?:!|$)", url.path)
                if pair:
                    return coordinate_pair(pair[1], pair[2])
        except ValueError:
            # Unsupported URLs use the same validation message below; never log pasted data.
            pass
    raise PlaceError(
        _("Paste decimal or DMS coordinates, or a map link containing coordinates.")
    )
