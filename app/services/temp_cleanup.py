"""Daily removal of stale files from the imports' temporary folders.

An import keeps the uploaded file in a temporary folder (upload_layout's
TEMP_DIRNAMES) between its upload/mapping step and the import itself, and
removes it when the import finishes — but an import abandoned halfway (the
page closed on the mapping or review step) leaves it behind for good. The
daily job (services.notification_service.run_daily_checks) deletes anything
older than TEMP_MAX_AGE_HOURS from those folders: an import still in
progress is never that old.
"""

from __future__ import annotations

import logging
import os
import time

from services.upload_layout import (  # pyright: ignore[reportMissingImports]
    TEMP_DIRNAMES,
    TEMP_MAX_AGE_HOURS,
)

log = logging.getLogger(__name__)


def cleanup_temp_uploads(
    upload_folder: str, max_age_hours: float = TEMP_MAX_AGE_HOURS
) -> int:
    """Delete files older than *max_age_hours* from the temporary import
    folders under *upload_folder*. Returns how many were deleted."""
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for dirname in sorted(TEMP_DIRNAMES):
        root = os.path.join(upload_folder, dirname)
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(dirpath, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                        removed += 1
                except FileNotFoundError:
                    continue  # removed meanwhile, e.g. by its import finishing
    if removed:
        log.info("Removed %d stale temporary import file(s)", removed)
    return removed
