"""Keep a rendered tracks GIF instead of rendering it on every download.

An aircraft's or a pilot's tracks GIF (utils.generate_tracks_gif) takes
seconds to draw — ~4 s for 87 tracks with the map tiles cached, ~25 s
without. It is kept under upload_layout.TRACKS_GIF_CACHE_DIRNAME, one file
per owner ("aircraft-12", "pilot-3") and variant (orientation and quality),
named after a hash of everything it was drawn from: the tracks with their
dates and airfields, the OpenAIP key (it adds the aeronautical layer) and
RENDER_VERSION. A new, changed or removed track — or a changed key — gives
another name, so that GIF is drawn again and the old file for that variant
is deleted. A GIF older than MAX_AGE_DAYS is drawn again too, to pick up
map updates, and the daily job deletes those (cleanup_stale_gifs). Never
backed up: it can always be drawn again.
"""

from __future__ import annotations

import contextlib
import glob
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from services.upload_layout import (  # pyright: ignore[reportMissingImports]
    TRACKS_GIF_CACHE_DIRNAME,
)

log = logging.getLogger(__name__)

# Bump when generate_tracks_gif draws differently, so kept GIFs are redrawn.
RENDER_VERSION = 1
MAX_AGE_DAYS = 30


def _fingerprint(inputs: Any) -> str:
    blob = json.dumps([RENDER_VERSION, inputs], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def cached_gif(
    upload_folder: str,
    owner: str,
    variant: str,
    inputs: Any,
    *,
    render: Callable[[], bytes],
) -> bytes:
    """The GIF drawn from *inputs* for *owner*'s *variant*: the kept one
    when it is there and recent, else ``render()``'s, kept for next time."""
    folder = os.path.join(upload_folder, TRACKS_GIF_CACHE_DIRNAME, owner)
    path = os.path.join(folder, f"{variant}-{_fingerprint(inputs)}.gif")
    try:
        if time.time() - os.path.getmtime(path) < MAX_AGE_DAYS * 86400:
            with open(path, "rb") as fh:
                return fh.read()
    except OSError:
        pass  # not kept yet (or unreadable): draw it
    data = render()
    try:
        os.makedirs(folder, exist_ok=True)
        for old in glob.glob(os.path.join(glob.escape(folder), f"{variant}-*.gif")):
            if old != path:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(old)
        tmp = f"{path}.{os.getpid()}.part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except OSError as exc:  # a full or read-only disk: just don't keep it
        log.debug("Could not keep the tracks GIF %s: %s", path, exc)
    return data


def cleanup_stale_gifs(upload_folder: str) -> int:
    """Delete kept GIFs older than MAX_AGE_DAYS (the daily job): ones nobody
    downloaded again since, an aircraft's long deleted included. Returns how
    many were deleted."""
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    root = os.path.join(upload_folder, TRACKS_GIF_CACHE_DIRNAME)
    removed = 0
    for path in glob.glob(os.path.join(glob.escape(root), "*", "*")):
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except FileNotFoundError:
            continue  # replaced meanwhile by a fresh render
    for folder in glob.glob(os.path.join(glob.escape(root), "*")):
        with contextlib.suppress(OSError):
            os.rmdir(folder)  # only when empty
    if removed:
        log.info("Removed %d stale tracks GIF(s)", removed)
    return removed
