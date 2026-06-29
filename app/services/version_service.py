"""
Version-check service — GitHub Pages versions list, AppSetting cache, background thread.

Kept in services/ so both init.py (thread start) and config/routes.py
(force-refresh endpoint) can import from here without creating a cycle.
"""

from typing import Any
from urllib.parse import urlparse

_VERSION_CHECK_HOST = "e2jk.github.io"
_VERSIONS_JSON_URL = f"https://{_VERSION_CHECK_HOST}/OpenHangar/versions.json"


def fetch_versions() -> list[str]:
    """Fetch the ordered list of all published versions from GitHub Pages. Returns [] on error."""
    import json
    import urllib.error
    import urllib.request

    class _StrictRedirect(urllib.request.HTTPRedirectHandler):
        """Block any redirect that leaves the allowed host."""

        def redirect_request(
            self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
        ) -> Any:
            host = urlparse(newurl).netloc
            if host != _VERSION_CHECK_HOST:
                raise urllib.error.URLError(
                    f"version-check redirect to {host!r} blocked"
                )
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(_StrictRedirect)
    req = urllib.request.Request(
        _VERSIONS_JSON_URL,
        headers={"User-Agent": "OpenHangar-version-check"},
    )
    try:
        with opener.open(req, timeout=10) as resp:  # nosec B310
            data = json.loads(resp.read())
            return data if isinstance(data, list) else []
    except Exception:
        return []


def fetch_latest_version() -> str | None:
    """Return the most recent published version, or None on error."""
    versions = fetch_versions()
    return versions[0] if versions else None


def upsert_app_setting(db_session: Any, key: str, value: str) -> None:
    from models import AppSetting  # pyright: ignore[reportMissingImports]

    setting = db_session.get(AppSetting, key)
    if setting:
        setting.value = value
    else:
        db_session.add(AppSetting(key=key, value=value))


def _persist_update_flag(db_session: Any, current: str, latest: str | None) -> None:
    """Compute update_available and write it to AppSetting. Silently ignores errors."""
    try:
        from packaging.version import Version  # pyright: ignore[reportMissingImports]

        available = bool(
            latest and current != "development" and Version(latest) > Version(current)
        )
    except Exception:
        available = False
    upsert_app_setting(db_session, "update_available", "true" if available else "false")


def run_version_check(app: Any) -> None:
    """Check GitHub Pages for the versions list and cache results in AppSetting."""
    import json
    import os
    from datetime import datetime, timedelta, timezone

    from models import AppSetting, db  # pyright: ignore[reportMissingImports]

    with app.app_context():
        last = db.session.get(AppSetting, "version_last_checked_at")
        if last and last.value:
            try:
                if datetime.now(timezone.utc) - datetime.fromisoformat(
                    last.value
                ) < timedelta(hours=23):
                    return
            except ValueError:
                pass  # malformed stored timestamp — proceed with the check

        versions = fetch_versions()
        upsert_app_setting(
            db.session,
            "version_last_checked_at",
            datetime.now(timezone.utc).isoformat(),
        )
        if versions:
            upsert_app_setting(db.session, "latest_version", versions[0])
            upsert_app_setting(db.session, "all_versions", json.dumps(versions))
            _persist_update_flag(
                db.session,
                os.environ.get("OPENHANGAR_VERSION", "development"),
                versions[0],
            )
        db.session.commit()


def startup_recompute_update_flag(app: Any) -> None:
    """Recompute update_available against the currently-running version.

    Called once at startup so that a freshly-deployed container immediately
    reflects the correct flag value without waiting for the next scheduled
    version check (which has a 0–6 h random delay).
    """
    import os

    from models import AppSetting, db  # pyright: ignore[reportMissingImports]

    with app.app_context():
        latest_s = db.session.get(AppSetting, "latest_version")
        latest = latest_s.value if latest_s else None
        _persist_update_flag(
            db.session, os.environ.get("OPENHANGAR_VERSION", "development"), latest
        )
        db.session.commit()


def version_check_loop(app: Any, _sleep_fn: Any = None) -> None:
    """Daemon thread body — random startup delay then every 24 h."""
    import random
    import time as _time

    sleep = _sleep_fn if _sleep_fn is not None else _time.sleep
    sleep(random.randint(0, 6 * 3600))
    while True:
        try:
            run_version_check(app)
        except Exception:
            app.logger.exception("Version check failed; will retry in 24 h")
        sleep(24 * 3600)


def start_version_check_thread(app: Any) -> None:
    import threading

    threading.Thread(
        target=startup_recompute_update_flag,
        args=(app,),
        daemon=True,
        name="version-flag-startup",
    ).start()
    threading.Thread(
        target=version_check_loop,
        args=(app,),
        daemon=True,
        name="version-check",
    ).start()
