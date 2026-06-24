"""
Background polling thread — automatically imports documents that arrive via
Syncthing (or any other file-sync tool) into the canonical folder structure.

Canonical path layout (relative to UPLOAD_FOLDER):
  {tenant_slug}/{aircraft_reg}/{category}/YYYY-MM-DD - title.ext

Behaviour on discovery:
  - File matches canonical structure AND aircraft is recognised in the tenant
    → Document row created immediately (auto-import).
  - File path is valid but aircraft/category cannot be resolved unambiguously
    → PendingReconcile entry created for manual review in the UI.
  - File already tracked (filename in documents table) or already pending
    → Skipped.

Enabled only when UPLOAD_FOLDER is set and the database is PostgreSQL
(SQLite = dev/test; the watcher is skipped there to avoid confusion).

Interval is configured via SYNC_SCAN_INTERVAL env var (default: 60 s).
"""

import contextlib
import logging
import mimetypes
import os
import re as _re
import threading
import time
from datetime import date as _date
from typing import Any

log = logging.getLogger("openhangar.sync_watcher")

_CATEGORY_VALUES: set[str] | None = None
_DATE_TITLE_RE = _re.compile(r"^(\d{4}-\d{2}-\d{2}) - (.+?)(\.[^.]+)?$")


def _categories() -> set[str]:
    global _CATEGORY_VALUES
    if _CATEGORY_VALUES is None:
        from models import DocCategory  # pyright: ignore[reportMissingImports]

        _CATEGORY_VALUES = set(DocCategory.ALL)
    return _CATEGORY_VALUES


def _scan_once(app: Any) -> None:
    """Single scan pass — runs inside an app context."""
    from models import (  # pyright: ignore[reportMissingImports]
        Aircraft,
        Document,
        PendingReconcile,
        Tenant,
        db,
    )

    folder = app.config.get("UPLOAD_FOLDER", "/data/uploads")
    if not os.path.isdir(folder):
        return

    with app.app_context():
        # Build lookup tables once per scan
        tenants = {
            t.slug: t for t in Tenant.query.filter(Tenant.slug.isnot(None)).all()
        }
        if not tenants:
            return

        known_filenames: set[str] = {
            doc.filename
            for doc in Document.query.with_entities(Document.filename).all()
        }
        pending_filepaths: set[str] = {
            pr.filepath
            for pr in PendingReconcile.query.with_entities(
                PendingReconcile.filepath
            ).all()
        }

        for tenant_slug, tenant in tenants.items():
            slug_dir = os.path.join(folder, tenant_slug)
            if not os.path.isdir(slug_dir):
                continue

            # Drop pending entries whose file no longer exists on disk
            for pr in PendingReconcile.query.filter_by(
                tenant_id=tenant.id, reconciled_at=None, ignored=False
            ).all():
                if not os.path.exists(os.path.join(folder, pr.filepath)):
                    db.session.delete(pr)
                    pending_filepaths.discard(pr.filepath)

            # Build registration → aircraft map for this tenant
            aircraft_by_reg: dict[str, Any] = {
                ac.registration.upper().replace("-", "").replace(" ", ""): ac
                for ac in Aircraft.query.filter_by(tenant_id=tenant.id).all()
            }

            for dirpath, _dirs, filenames in os.walk(slug_dir):
                for fname in filenames:
                    if fname.startswith(".") or fname.startswith("_"):
                        continue

                    full = os.path.join(dirpath, fname)
                    relpath = os.path.relpath(full, folder).replace("\\", "/")

                    if relpath in known_filenames or relpath in pending_filepaths:
                        continue

                    _process_file(
                        app,
                        full,
                        relpath,
                        fname,
                        tenant,
                        aircraft_by_reg,
                        known_filenames,
                        pending_filepaths,
                        db,
                        Document,
                        PendingReconcile,
                    )

        db.session.commit()


def _process_file(  # noqa: PLR0913
    app: Any,
    full_path: str,
    relpath: str,
    fname: str,
    tenant: Any,
    aircraft_by_reg: dict[str, Any],
    known_filenames: set[str],
    pending_filepaths: set[str],
    db: Any,
    Document: Any,
    PendingReconcile: Any,
) -> None:
    """Decide whether to auto-import or queue for review."""
    parts = relpath.split("/")
    # Expected: slug / reg / category / filename  (4 parts minimum)
    if len(parts) < 4:
        _queue_pending(
            relpath, fname, None, None, None, None, tenant, db, PendingReconcile
        )
        pending_filepaths.add(relpath)
        return

    reg_raw = parts[1].upper().replace("-", "").replace(" ", "")
    cat_str = parts[2]
    filename_part = parts[3]

    aircraft = aircraft_by_reg.get(reg_raw)
    cat_lower = cat_str.lower()
    category = cat_lower if cat_lower in _categories() else None

    # Parse "YYYY-MM-DD - title.ext" from the filename
    m = _DATE_TITLE_RE.match(filename_part)
    title_hint: str | None = None
    date_hint: _date | None = None
    if m:
        with contextlib.suppress(
            ValueError
        ):  # regex matched date-like string but it's invalid (e.g. month 13); treat as no date
            date_hint = _date.fromisoformat(m.group(1))
        title_hint = m.group(2)
    else:
        title_hint = os.path.splitext(filename_part)[0]

    if aircraft and category:
        # Fully resolved — auto-import immediately
        mime = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        size = None
        with contextlib.suppress(
            OSError
        ):  # file may have disappeared between scan and import; size stays None
            size = os.path.getsize(full_path)
        doc = Document(
            aircraft_id=aircraft.id,
            filename=relpath,
            original_filename=fname,
            mime_type=mime,
            size_bytes=size,
            title=title_hint,
            category=category,
        )
        db.session.add(doc)
        known_filenames.add(relpath)
        log.info(
            "sync_watcher: auto-imported %s → aircraft=%s category=%s",
            relpath,
            aircraft.registration,
            category,
        )
    else:
        # Ambiguous — queue for manual review
        _queue_pending(
            relpath,
            fname,
            aircraft,
            category,
            title_hint,
            date_hint,
            tenant,
            db,
            PendingReconcile,
        )
        pending_filepaths.add(relpath)
        log.info(
            "sync_watcher: queued for review %s (aircraft=%s category=%s)",
            relpath,
            aircraft.registration if aircraft else "?",
            category or "?",
        )


def _queue_pending(
    relpath: str,
    fname: str,
    aircraft: Any,
    category: str | None,
    title_hint: str | None,
    date_hint: _date | None,
    tenant: Any,
    db: Any,
    PendingReconcile: Any,
) -> None:
    pr = PendingReconcile(
        tenant_id=tenant.id,
        aircraft_id=aircraft.id if aircraft else None,
        filepath=relpath,
        category=category,
        title_hint=title_hint or os.path.splitext(fname)[0],
        date_hint=date_hint,
    )
    db.session.add(pr)


def _watcher_loop(app: Any, interval: int) -> None:
    log.info("sync_watcher: started (interval=%ds)", interval)
    while True:
        try:
            _scan_once(app)
        except Exception:
            log.exception("sync_watcher: unhandled error during scan")
        time.sleep(interval)


def start_sync_watcher(app: Any) -> None:
    """Start the background sync watcher thread (idempotent, daemon thread)."""
    try:
        interval = int(os.environ.get("OPENHANGAR_SYNC_SCAN_INTERVAL", "60"))
    except ValueError:
        interval = 60

    t = threading.Thread(
        target=_watcher_loop,
        args=(app, interval),
        daemon=True,
        name="sync-watcher",
    )
    t.start()
