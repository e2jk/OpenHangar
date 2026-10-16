"""Names of the folders the app itself writes under UPLOAD_FOLDER, next to
the documents of the Syncthing-compatible <tenant-slug>/<REG>/<category>/
layout.

Both document scanners (the manual reconcile scan and the background
sync_watcher) walk each tenant folder and treat every file they don't
already know as a document to import or reconcile — they must skip these.
"""

from __future__ import annotations

# <tenant-slug>/<REG>/photos/ — aircraft photos (AircraftPhoto rows).
PHOTOS_DIRNAME = "photos"
# <tenant-slug>/<REG>/gps_logs/ — original GPS log files (services.gps_archive).
GPS_ARCHIVE_DIRNAME = "gps_logs"
# _pilots/<user-id>/gps_logs/ — a pilot's own original GPS log files. Tenant
# slugs are [a-z0-9-] only, so this top-level folder can never clash with a
# tenant's folder (nor is it walked by the scanners, which only walk those).
PILOT_ARCHIVE_ROOT = "_pilots"

_APP_MANAGED_AIRCRAFT_DIRS = frozenset({PHOTOS_DIRNAME, GPS_ARCHIVE_DIRNAME})


def is_app_managed(relpath: str) -> bool:
    """True for a file (path relative to UPLOAD_FOLDER, "/"-separated) in
    one of an aircraft's app-managed folders — never a document."""
    parts = relpath.split("/")
    return len(parts) >= 4 and parts[2] in _APP_MANAGED_AIRCRAFT_DIRS
