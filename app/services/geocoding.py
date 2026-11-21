"""Address and place-name search through a Nominatim-compatible geocoder.

Searches always go through the OpenHangar server, never from the browser: the
user's IP address is not exposed to the geocoder and the page's CSP keeps
`connect-src 'self'`. Enabled by default with the public OpenStreetMap
Nominatim service, within its usage policy
(https://operations.osmfoundation.org/policies/nominatim/):

- a search only when the user asks for one (callers must not search as the
  user types);
- at most one request per second for the whole instance, shared by every
  worker process through a file lock;
- an identifying User-Agent and the "© OpenStreetMap contributors"
  attribution next to the results (`attribution()`);
- repeated searches are answered from a cache.

`OPENHANGAR_GEOCODER_URL` (`app.config["GEOCODER_URL"]`) points to another
Nominatim-compatible server, or disables address search when empty. The
search text is never logged and never written to disk: the cache lives in
memory only, per worker.

Entry points: `enabled()`, `search(query, language=...)`, `attribution()`,
and `GeocoderError` (its `reason` and translated `message`).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from flask import current_app
from flask_babel import gettext as _

from services.upload_layout import GEOCODER_LOCKS_DIRNAME

DEFAULT_URL = "https://nominatim.openstreetmap.org"
USER_AGENT = "OpenHangar address search (+https://github.com/e2jk/OpenHangar)"

MIN_QUERY = 2
MAX_QUERY = 200
MAX_RESULTS = 10
REQUEST_SECONDS = 6.0
# How long a search waits for the instance-wide one-request-per-second slot.
SLOT_WAIT_SECONDS = 2.5
MIN_INTERVAL_SECONDS = 1.0
MAX_RESPONSE_BYTES = 1_000_000
CACHE_ENTRIES = 256
CACHE_SECONDS = 24 * 3600

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeocodeResult:
    """One search result: what a form needs to drop a pin and pre-fill fields."""

    name: str
    address: str
    latitude: float
    longitude: float
    category: str
    osm_reference: str  # e.g. "N123456" — stable OpenStreetMap object reference


class GeocoderError(Exception):
    """A search that could not run. `reason` is one of:

    - ``disabled``: address search is turned off on this instance;
    - ``invalid``: the search text is empty, too short or too long;
    - ``busy``: other searches are using the instance's one-per-second slot;
    - ``unavailable``: the geocoder could not be reached or answered badly.

    The map pin and typed coordinates keep working in every case.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @property
    def message(self) -> str:
        messages = {
            "disabled": _(
                "Address search is turned off on this instance. Place the pin on the map or enter coordinates."
            ),
            "invalid": _("Enter between 2 and 200 characters to search for."),
            "busy": _(
                "Address search is busy. Try again in a moment, or place the pin on the map."
            ),
            "unavailable": _(
                "Address search is unavailable right now. Place the pin on the map or enter coordinates."
            ),
        }
        return str(messages.get(self.reason, messages["unavailable"]))


def base_url() -> str:
    """The configured geocoder, or "" when address search is disabled."""
    url = str(current_app.config.get("GEOCODER_URL", DEFAULT_URL) or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ""
    return url.rstrip("/")


def enabled() -> bool:
    return bool(base_url())


def attribution() -> str:
    """Shown next to results: the data comes from OpenStreetMap."""
    return str(_("Address search: © OpenStreetMap contributors"))


def _clean_query(query: str) -> str:
    text = " ".join((query or "").split())
    if (
        len(text) < MIN_QUERY
        or len(text) > MAX_QUERY
        or any(ord(c) < 32 or ord(c) == 127 for c in text)
    ):
        raise GeocoderError("invalid")
    return text


class _Cache:
    """A small per-worker LRU cache with expiry, never written to disk."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: OrderedDict[tuple[str, ...], tuple[float, list[GeocodeResult]]]
        self._rows = OrderedDict()

    def get(self, key: tuple[str, ...]) -> list[GeocodeResult] | None:
        with self._lock:
            row = self._rows.get(key)
            if row is None:
                return None
            stored, results = row
            if time.monotonic() - stored > CACHE_SECONDS:
                del self._rows[key]
                return None
            self._rows.move_to_end(key)
            return list(results)

    def put(self, key: tuple[str, ...], results: list[GeocodeResult]) -> None:
        with self._lock:
            self._rows[key] = (time.monotonic(), list(results))
            self._rows.move_to_end(key)
            while len(self._rows) > CACHE_ENTRIES:
                self._rows.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()


_cache = _Cache()


@contextlib.contextmanager
def _request_slot() -> Iterator[None]:
    """At most one geocoder request per second for the whole instance.

    Every worker shares one lock file in the uploads folder: the holder waits
    until a second has passed since the previous request (its time is kept in
    the file), sends its request and only then releases the lock. A search
    that cannot get the lock within a few seconds is refused as busy rather
    than queuing indefinitely.
    """
    folder = Path(current_app.config["UPLOAD_FOLDER"]) / GEOCODER_LOCKS_DIRNAME
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        handle = (folder / "requests.lock").open("a+")
    except OSError as exc:
        raise GeocoderError("unavailable") from exc
    with handle:
        deadline = time.monotonic() + SLOT_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise GeocoderError("busy") from exc
                time.sleep(0.05)
        try:
            handle.seek(0)
            try:
                previous = float(handle.read().strip() or 0)
            except ValueError:
                previous = 0.0
            # Never wait longer than the interval, even after a clock change.
            wait = min(
                MIN_INTERVAL_SECONDS, previous + MIN_INTERVAL_SECONDS - time.time()
            )
            if wait > 0:
                time.sleep(wait)
            handle.seek(0)
            handle.truncate()
            handle.write(f"{time.time():.3f}")
            handle.flush()
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _results(payload: Any) -> list[GeocodeResult]:
    if not isinstance(payload, list):
        raise GeocoderError("unavailable")
    results = []
    for row in payload[:MAX_RESULTS]:
        if not isinstance(row, dict):
            continue
        try:
            latitude = float(row["lat"])
            longitude = float(row["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue
        address = str(row.get("display_name") or "").strip()
        name = str(row.get("name") or "").strip() or address.split(",")[0].strip()
        osm_type = str(row.get("osm_type") or "")[:1].upper()
        osm_id = str(row.get("osm_id") or "")
        results.append(
            GeocodeResult(
                name=name[:200],
                address=address[:500],
                latitude=latitude,
                longitude=longitude,
                category=str(row.get("category") or row.get("class") or "")[:64],
                osm_reference=f"{osm_type}{osm_id}"
                if osm_type in {"N", "W", "R"} and osm_id.isdigit()
                else "",
            )
        )
    return results


def search(query: str, *, language: str = "en", limit: int = 5) -> list[GeocodeResult]:
    """Places matching *query*, best first. Raises `GeocoderError`.

    Call it only when the user submits a search, never while typing.
    """
    url = base_url()
    if not url:
        raise GeocoderError("disabled")
    text = _clean_query(query)
    limit = max(1, min(int(limit), MAX_RESULTS))
    language = (language or "en")[:16]
    key = (url, text.casefold(), language, str(limit))
    cached = _cache.get(key)
    if cached is not None:
        return cached
    params = urlencode(
        {
            "q": text,
            "format": "jsonv2",
            "limit": limit,
            "addressdetails": 0,
            "accept-language": language,
        }
    )
    request = urllib.request.Request(
        f"{url}/search?{params}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with _request_slot():
        try:
            with urllib.request.urlopen(  # nosec B310  # http(s) only, checked in base_url
                request, timeout=REQUEST_SECONDS
            ) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            # Never log the search text: only what went wrong.
            _log.warning("Address search failed: geocoder answered HTTP %s", exc.code)
            raise GeocoderError("busy" if exc.code == 429 else "unavailable") from exc
        except (OSError, ValueError) as exc:
            _log.warning("Address search failed: %s", type(exc).__name__)
            raise GeocoderError("unavailable") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise GeocoderError("unavailable")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise GeocoderError("unavailable") from exc
    results = _results(payload)
    _cache.put(key, results)
    return list(results)
