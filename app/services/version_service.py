"""
Version-check service — GitHub Releases polling, AppSetting cache, background thread.

Kept in services/ so both init.py (thread start) and config/routes.py
(force-refresh endpoint) can import from here without creating a cycle.
"""

from typing import Any
from urllib.parse import urlparse

_VERSION_CHECK_HOST = "api.github.com"


def fetch_latest_version() -> str | None:
    """Query GitHub Releases API for the latest published tag. Returns bare version or None."""
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
        f"https://{_VERSION_CHECK_HOST}/repos/e2jk/OpenHangar/releases/latest",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "OpenHangar-version-check",
        },
    )
    try:
        with opener.open(req, timeout=10) as resp:  # nosec B310
            data = json.loads(resp.read())
            tag = data.get("tag_name", "")
            return tag.lstrip("v") or None
    except Exception:
        return None


def upsert_app_setting(db_session: Any, key: str, value: str) -> None:
    from models import AppSetting  # pyright: ignore[reportMissingImports]

    setting = db_session.get(AppSetting, key)
    if setting:
        setting.value = value
    else:
        db_session.add(AppSetting(key=key, value=value))


def run_version_check(app: Any) -> None:
    """Check GitHub for the latest release and cache result in AppSetting."""
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

        latest = fetch_latest_version()
        upsert_app_setting(
            db.session,
            "version_last_checked_at",
            datetime.now(timezone.utc).isoformat(),
        )
        if latest:
            upsert_app_setting(db.session, "latest_version", latest)
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

    t = threading.Thread(
        target=version_check_loop,
        args=(app,),
        daemon=True,
        name="version-check",
    )
    t.start()
