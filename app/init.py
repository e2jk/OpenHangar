import os
import secrets
import sqlite3
from datetime import timedelta
from functools import lru_cache

import click  # pyright: ignore[reportMissingImports]
from typing import Any
from urllib.parse import urlparse

from flask import (
    Flask,
    Response,
    g,
    has_request_context,
    render_template,
    request,
    send_from_directory,
    session,
)  # pyright: ignore[reportMissingImports]
from flask.typing import ResponseReturnValue  # pyright: ignore[reportMissingImports]
from flask_babel import Babel, get_locale as _babel_get_locale  # pyright: ignore[reportMissingImports]
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect  # pyright: ignore[reportMissingImports]
from werkzeug.middleware.proxy_fix import ProxyFix  # pyright: ignore[reportMissingImports]
from sqlalchemy import event  # pyright: ignore[reportMissingImports]
from sqlalchemy.engine import Engine  # pyright: ignore[reportMissingImports]

SUPPORTED_LOCALES = ["en", "fr", "nl"]

LOCALE_META = {
    "en": {"flag": "🇬🇧", "abbr": "EN", "native": "English", "english": "English"},
    "fr": {"flag": "🇫🇷", "abbr": "FR", "native": "Français", "english": "French"},
    "nl": {"flag": "🇳🇱", "abbr": "NL", "native": "Nederlands", "english": "Dutch"},
}

# EE-09: aviation history days — (month, day, msgid).  Add new entries here.
_AVIATION_DAYS: list[tuple[int, int, str]] = [
    (3, 2, "First flight of Concorde — André Turcat at the controls, Toulouse (1969)"),
    (
        5,
        21,
        "Charles Lindbergh lands at Le Bourget — first solo transatlantic flight (1927)",
    ),
    (
        7,
        25,
        "Louis Blériot crosses the English Channel — first crossing by airplane (1909)",
    ),
    (
        11,
        21,
        "Pilâtre de Rozier & d'Arlandes — first manned free balloon flight, Paris (1783)",
    ),
    (12, 17, "First flight: 17 Dec 1903 — 12 seconds, 37 metres. (Wright Brothers)"),
]


def _aviation_day_msgid(month: int, day: int) -> str | None:
    for m, d, msgid in _AVIATION_DAYS:
        if m == month and d == day:
            return msgid
    return None


@lru_cache(maxsize=None)
def _static_folder_mtime_token(static_folder: str) -> str:
    latest = 0
    for root, _dirs, files in os.walk(static_folder):
        for name in files:
            try:
                mtime = int(os.path.getmtime(os.path.join(root, name)))
            except OSError:  # file removed while walking
                continue
            latest = max(latest, mtime)
    return str(latest)


def _static_cache_version(static_folder: str) -> str:
    """Cache-busting token appended to static URLs (?v=…).

    The release version when running a published image; otherwise the newest
    file mtime under the static folder — stable across gunicorn workers, and
    changes whenever an asset changes during development."""
    version = os.environ.get("OPENHANGAR_VERSION", "")
    if version and version != "development":
        return version
    return _static_folder_mtime_token(static_folder)


@event.listens_for(Engine, "connect")
def _set_sqlite_fk_pragma(dbapi_connection: Any, _record: Any) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def _drop_and_restore_schema(database_url: str, sql_bytes: bytes) -> None:
    """Drop the public schema and restore it from a pg_dump byte-string."""
    import subprocess  # noqa: PLC0415  # nosec B404
    import tempfile

    from sqlalchemy import text  # pyright: ignore[reportMissingImports]

    from models import db  # pyright: ignore[reportMissingImports]

    # Close the ORM session while its connection is still alive so Flask's
    # teardown has nothing left to rollback after we terminate other backends.
    db.session.remove()

    with db.engine.connect() as conn:
        # Terminate all other connections so DROP SCHEMA can acquire its
        # ACCESS EXCLUSIVE lock even if the web server left a connection
        # idle-in-transaction (which would block indefinitely otherwise).
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                " WHERE datname = current_database() AND pid != pg_backend_pid()"
            )
        )
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.commit()

    # Dispose the connection pool so no SQLAlchemy connections linger and
    # block psql's DDL statements with AccessShareLock.
    db.engine.dispose()

    # Strip ownership and privilege statements from the dump.  pg_dump (without
    # --no-owner / --no-acl) records the source role for every object and emits
    # GRANT/REVOKE commands, but those role names are environment-specific and
    # do not exist on the target server.  New backups are produced with
    # --no-owner --no-acl, but we strip here too as a safety net for archives
    # made before that change.
    import re

    sql_bytes = re.sub(
        rb"^(?:ALTER\s+\S[^\n]*\bOWNER\s+TO\b|GRANT\b|REVOKE\b)[^\n]*;\s*$",
        b"",
        sql_bytes,
        flags=re.MULTILINE | re.IGNORECASE,
    )

    # Write the dump to a temp file so psql can read it directly rather than
    # via stdin — avoids pipe-buffering hangs on large dumps and lets psql
    # print progress to the terminal in real time.
    with tempfile.NamedTemporaryFile(suffix=".sql", delete=False) as tmp:
        tmp.write(sql_bytes)
        tmp_path = tmp.name

    try:
        result = subprocess.run(  # nosec B603
            ["psql", "--no-password", "-f", tmp_path, database_url],
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("psql restore timed out after 10 minutes.")
    finally:
        os.unlink(tmp_path)

    if result.returncode != 0:
        raise RuntimeError(f"psql exited with code {result.returncode}")


def _easa_sync_loop(app: Flask) -> None:
    import logging
    import os
    import random
    import time
    from datetime import datetime, timedelta, timezone

    from airworthiness_sync import sync_all_nodes  # pyright: ignore[reportMissingImports]

    _log = logging.getLogger(__name__)

    # Determine the daily sync time (UTC).  Admin can pin a specific hour via
    # OPENHANGAR_AIRWORTHINESS_EASA_SYNC_HOUR (0-23).  Default: random hour
    # 01-05 UTC so that different instances do not all hit EASA simultaneously.
    env_hour = os.environ.get("OPENHANGAR_AIRWORTHINESS_EASA_SYNC_HOUR")
    if env_hour is not None:
        try:
            sync_hour = int(env_hour) % 24
        except ValueError:
            sync_hour = random.randint(1, 5)
    else:
        sync_hour = random.randint(1, 5)
    sync_minute = random.randint(0, 59)
    _log.info("EASA sync scheduled daily at %02d:%02d UTC", sync_hour, sync_minute)

    while True:
        now = datetime.now(timezone.utc)
        next_run = now.replace(
            hour=sync_hour, minute=sync_minute, second=0, microsecond=0
        )
        if next_run <= now:
            next_run += timedelta(days=1)
        time.sleep((next_run - datetime.now(timezone.utc)).total_seconds())
        sync_all_nodes(app)


def _start_easa_sync_scheduler(app: Flask) -> None:
    import threading

    t = threading.Thread(
        target=_easa_sync_loop,
        args=(app,),
        daemon=True,
        name="easa-sync",
    )
    t.start()


def _parse_notification_time() -> tuple[int, int]:
    """Return (hour, minute) from OPENHANGAR_NOTIFICATION_TIME (HH:MM, default 07:00).

    Raises ValueError with a human-readable message if the value is set but invalid.
    """
    raw = os.environ.get("OPENHANGAR_NOTIFICATION_TIME", "07:00")
    err = f"OPENHANGAR_NOTIFICATION_TIME={raw!r} is invalid — expected HH:MM (e.g. '07:00')"
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValueError(err)
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(err)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(err)
    return hour, minute


def _notification_daily_loop(app: Flask, run_hour: int, run_minute: int) -> None:
    import logging
    import time
    from datetime import datetime, timedelta, timezone

    _log = logging.getLogger(__name__)
    _log.info(
        "Notification daily check scheduled at %02d:%02d UTC", run_hour, run_minute
    )

    while True:
        now = datetime.now(timezone.utc)
        next_run = now.replace(
            hour=run_hour, minute=run_minute, second=0, microsecond=0
        )
        if next_run <= now:
            next_run += timedelta(days=1)
        time.sleep((next_run - datetime.now(timezone.utc)).total_seconds())
        try:
            from services.notification_service import run_daily_checks  # pyright: ignore[reportMissingImports]

            run_daily_checks(app)
        except Exception:
            _log.exception("Notification daily check failed; will retry tomorrow")


def _start_notification_scheduler(app: Flask) -> None:
    import threading

    run_hour, run_minute = _parse_notification_time()
    t = threading.Thread(
        target=_notification_daily_loop,
        args=(app, run_hour, run_minute),
        daemon=True,
        name="notification-daily",
    )
    t.start()


def create_app() -> Flask:
    from security_alerts import attach_to_logger  # pyright: ignore[reportMissingImports]

    attach_to_logger()

    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]

    # Propagate OPENHANGAR_ENV → FLASK_ENV so Flask's own internals keep working.
    os.environ["FLASK_ENV"] = os.environ.get("OPENHANGAR_ENV", "production")

    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "OPENHANGAR_DATABASE_URL", "sqlite:///:memory:"
    )
    secret_key = os.environ.get("OPENHANGAR_SECRET_KEY")
    if not secret_key:
        raise RuntimeError("OPENHANGAR_SECRET_KEY environment variable must be set")
    if "change" in secret_key.lower():
        raise RuntimeError(
            "OPENHANGAR_SECRET_KEY appears to be a placeholder value. "
            "Generate a real key with: openssl rand -hex 32"
        )
    app.config["SECRET_KEY"] = secret_key
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["UPLOAD_FOLDER"] = os.environ.get(
        "OPENHANGAR_UPLOAD_FOLDER", "/data/uploads"
    )
    app.config["BACKUP_FOLDER"] = os.environ.get(
        "OPENHANGAR_BACKUP_FOLDER", "/data/backups"
    )
    app.config["MAX_CONTENT_LENGTH"] = (
        50 * 1024 * 1024
    )  # overridden by _validate_config
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    _session_days = int(os.environ.get("OPENHANGAR_SESSION_LIFETIME_DAYS", "30"))
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=_session_days)

    flask_env = os.environ.get("OPENHANGAR_ENV", "production")

    if flask_env in ("development", "test"):
        app.config["TEMPLATES_AUTO_RELOAD"] = True

    from models import db

    db.init_app(app)
    Migrate(app, db)

    def _get_locale() -> str | None:
        if not has_request_context():
            return "en"
        if session.get("user_id"):
            # Demo sessions: visitor's session language takes precedence over the
            # demo user's stored default so Accept-Language / manual switcher work.
            if (
                session.get("demo_slot_id")
                and session.get("language") in SUPPORTED_LOCALES
            ):
                return str(session["language"])
            from models import User

            user = db.session.get(User, session["user_id"])
            if user and user.language in SUPPORTED_LOCALES:
                return str(user.language)
        if session.get("language") in SUPPORTED_LOCALES:
            return str(session["language"])
        return str(request.accept_languages.best_match(SUPPORTED_LOCALES, default="en"))

    Babel(app, locale_selector=_get_locale)
    CSRFProtect(app)

    from extensions import cache as _cache  # pyright: ignore[reportMissingImports]
    from extensions import limiter as _limiter  # pyright: ignore[reportMissingImports]

    app.config["CACHE_TYPE"] = "SimpleCache"
    app.config["CACHE_DEFAULT_TIMEOUT"] = 300
    _cache.init_app(app)
    _limiter.init_app(app)

    static_version = _static_cache_version(app.static_folder or "static")

    @app.url_defaults
    def _static_cache_bust(endpoint: str, values: dict[str, Any]) -> None:
        if endpoint == "static":
            values.setdefault("v", static_version)

    @app.before_request
    def _generate_csp_nonce() -> None:
        g.csp_nonce = secrets.token_urlsafe(16)

    def _csp_nonce() -> str:
        return getattr(g, "csp_nonce", "")

    app.jinja_env.globals["csp_nonce"] = _csp_nonce

    @app.after_request
    def _security_headers(response: Any) -> Any:
        nonce = getattr(g, "csp_nonce", "")
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; "
            f"script-src 'nonce-{nonce}'; "
            f"worker-src 'self' blob:; "
            f"style-src-elem 'self'; "
            f"style-src-attr 'none'; "
            f"font-src 'self'; "
            f"img-src 'self' data: blob: tile.openstreetmap.org *.basemaps.cartocdn.com api.tiles.openaip.net; "
            f"connect-src 'self'; "
            f"object-src 'none'; "
            f"base-uri 'self'; "
            f"form-action 'self'; "
            f"frame-ancestors 'none';"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if request.endpoint == "static" and response.status_code in (200, 304):
            # Static URLs carry the ?v= cache-buster, so long-lived caching is
            # safe; uploads/documents go through their own routes and keep the
            # authenticated no-store below.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif session.get("user_id"):
            existing_cc = response.headers.get("Cache-Control", "")
            if "public" not in existing_cc and "immutable" not in existing_cc:
                response.headers["Cache-Control"] = "no-store, private"
        return response

    from flask_babel import format_date, format_datetime, format_decimal

    app.jinja_env.globals.update(
        format_date=format_date,
        format_datetime=format_datetime,
        format_decimal=format_decimal,
    )

    if app.config.get("TESTING") or os.environ.get("OPENHANGAR_ENV") == "development":
        from jinja2 import StrictUndefined

        app.jinja_env.undefined = StrictUndefined

    from utils import (
        _load_aircraft_type_variants,
        _load_airport_names,
    )

    @app.template_filter("airport_name")
    def _airport_name_filter(code: str | None) -> str:
        if not code:
            return ""
        return _load_airport_names().get(code.upper(), "")

    @app.route("/manifest.json")
    def pwa_manifest() -> ResponseReturnValue:
        from flask import jsonify as _jsonify

        return _jsonify(
            {
                "name": "OpenHangar",
                "short_name": "OpenHangar",
                "description": "Open-source aircraft operations and pilot logbook",
                "start_url": "/",
                "display": "standalone",
                "theme_color": "#1a3a5c",
                "background_color": "#1a3a5c",
                "icons": [
                    {
                        "src": "/static/icons/icon.svg",
                        "sizes": "any",
                        "type": "image/svg+xml",
                    },
                    {
                        "src": "/static/icons/icon-maskable.svg",
                        "sizes": "any",
                        "type": "image/svg+xml",
                        "purpose": "maskable",
                    },
                ],
                "share_target": {
                    "action": "/pwa/shared",
                    "method": "POST",
                    "enctype": "multipart/form-data",
                    "params": {
                        "title": "title",
                        "text": "text",
                        "url": "url",
                        "files": [
                            {
                                "name": "files",
                                "accept": ["application/pdf", "image/*"],
                            }
                        ],
                    },
                },
                "shortcuts": [
                    {
                        "name": "Log a Flight",
                        "short_name": "Log Flight",
                        "url": "/flights/new",
                        "icons": [
                            {
                                "src": "/static/icons/shortcut-log-flight.svg",
                                "sizes": "any",
                                "type": "image/svg+xml",
                            }
                        ],
                    },
                    {
                        "name": "My Aircraft",
                        "short_name": "Aircraft",
                        "url": "/aircraft",
                        "icons": [
                            {
                                "src": "/static/icons/shortcut-aircraft.svg",
                                "sizes": "any",
                                "type": "image/svg+xml",
                            }
                        ],
                    },
                    {
                        "name": "Documents",
                        "short_name": "Documents",
                        "url": "/documents",
                        "icons": [
                            {
                                "src": "/static/icons/shortcut-documents.svg",
                                "sizes": "any",
                                "type": "image/svg+xml",
                            }
                        ],
                    },
                ],
            }
        )

    @app.route("/sw.js")
    def service_worker() -> ResponseReturnValue:
        sw_path = os.path.join(app.static_folder or "static", "js", "sw.js")
        with open(sw_path, encoding="utf-8") as fh:
            content = fh.read()
        version = os.environ.get("OPENHANGAR_VERSION", "")
        cache_name = (
            f"openhangar-{version}"
            if version and version != "development"
            else f"openhangar-{secrets.token_hex(8)}"
        )
        content = content.replace("__SW_CACHE_VERSION__", cache_name)
        response = Response(content, mimetype="application/javascript")
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.route("/api/check-flight-duplicate")
    def api_check_flight_duplicate() -> ResponseReturnValue:
        from flask import jsonify as _jsonify

        if not session.get("user_id"):
            return _jsonify({"error": "unauthorized"}), 401
        date_str = request.args.get("date", "")
        aircraft_id_str = request.args.get("aircraft_id", "")
        dep = request.args.get("departure_icao", "")
        arr = request.args.get("arrival_icao", "")
        if not (date_str and dep and arr):
            return _jsonify({"duplicate": False})
        from models import Aircraft, FlightEntry, PilotLogbookEntry, TenantUser

        uid = int(session["user_id"])
        try:
            from datetime import date as _date

            flight_date = _date.fromisoformat(date_str)
        except ValueError:
            return _jsonify({"duplicate": False})

        tu = TenantUser.query.filter_by(user_id=uid).first()

        if tu and aircraft_id_str and aircraft_id_str.isdigit():
            ac_id = int(aircraft_id_str)
            # Scope by tenant: only match flights on an aircraft the caller's
            # tenant owns, otherwise this leaks a cross-tenant existence oracle.
            owned = Aircraft.query.filter_by(id=ac_id, tenant_id=tu.tenant_id).first()
            if owned:
                dup = FlightEntry.query.filter_by(
                    aircraft_id=ac_id,
                    date=flight_date,
                    departure_icao=dep,
                    arrival_icao=arr,
                ).first()
                if dup:
                    return _jsonify({"duplicate": True})

        if tu:
            dup_pilot = PilotLogbookEntry.query.filter_by(
                pilot_user_id=uid,
                date=flight_date,
                departure_place=dep,
                arrival_place=arr,
            ).first()
            if dup_pilot:
                return _jsonify({"duplicate": True})

        return _jsonify({"duplicate": False})

    @app.route("/airport-search")
    def airport_search() -> ResponseReturnValue:
        if not session.get("user_id"):
            return {"results": []}
        q = request.args.get("q", "").strip()
        if len(q) < 2:
            return {"results": []}
        q_code = q.upper()
        q_low = q.lower()
        names = _load_airport_names()
        code_hits: list[dict[str, str]] = []
        name_hits: list[dict[str, str]] = []
        for code, name in names.items():
            if code.startswith(q_code):
                code_hits.append({"code": code, "name": name})
            elif q_low in name.lower():
                name_hits.append({"code": code, "name": name})
        return {"results": (code_hits + name_hits)[:10]}

    @app.route("/aircraft-type-search")
    def aircraft_type_search() -> ResponseReturnValue:
        if not session.get("user_id"):
            return {"results": []}
        q = request.args.get("q", "").strip()
        if len(q) < 2:
            return {"results": []}
        q_up = q.upper()
        words = q.lower().split()
        variants = _load_aircraft_type_variants()
        code_hits: list[dict[str, str]] = []
        name_hits: list[dict[str, str]] = []
        for des, full_name, mfr, mdl in variants:
            name_low = full_name.lower()
            entry = {"code": des, "name": full_name, "manufacturer": mfr, "model": mdl}
            if des.startswith(q_up):
                code_hits.append(entry)
            elif all(w in name_low for w in words):
                name_hits.append(entry)
        return {"results": code_hits + name_hits}

    from auth.routes import auth_bp

    app.register_blueprint(auth_bp)

    from aircraft.routes import aircraft_bp

    app.register_blueprint(aircraft_bp)

    from flights.routes import flights_bp

    app.register_blueprint(flights_bp)

    from maintenance.routes import maintenance_bp

    app.register_blueprint(maintenance_bp)

    from expenses.routes import expenses_bp

    app.register_blueprint(expenses_bp)

    from documents.routes import documents_bp

    app.register_blueprint(documents_bp)

    from config.routes import config_bp

    app.register_blueprint(config_bp)

    from share.routes import share_bp

    app.register_blueprint(share_bp)

    from snags.routes import snags_bp

    app.register_blueprint(snags_bp)

    from pilots.routes import pilots_bp

    app.register_blueprint(pilots_bp)

    from users.routes import users_bp

    app.register_blueprint(users_bp)

    from reservations.routes import reservations_bp

    app.register_blueprint(reservations_bp)

    from squawk.routes import squawk_bp

    app.register_blueprint(squawk_bp)

    from hangar.routes import hangar_bp

    app.register_blueprint(hangar_bp)

    from airworthiness.routes import airworthiness_bp

    app.register_blueprint(airworthiness_bp)

    from pwa.routes import pwa_bp

    app.register_blueprint(pwa_bp)

    if flask_env == "demo":
        from demo.routes import demo_bp

        app.register_blueprint(demo_bp)

    def _current_theme(
        user_obj: Any, in_request: bool, sess: Any, is_demo: bool
    ) -> str:
        if in_request and user_obj and not is_demo and not sess.get("demo_slot_id"):
            t = getattr(user_obj, "theme", None)
            if t in ("light", "dark"):
                return str(t)
        if in_request:
            t = sess.get("theme")
            if t in ("light", "dark", "system"):
                return str(t)
        return "system"

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        from models import DemoSlot, Role, TenantProfile, TenantUser, User
        from utils import check_update_available, current_user_role

        is_demo = flask_env == "demo"
        demo_next_wipe_utc = (
            os.environ.get("OPENHANGAR_DEMO_NEXT_WIPE_UTC") if is_demo else None
        )
        demo_site_url = os.environ.get("OPENHANGAR_DEMO_SITE_URL")
        repo_url = os.environ.get(
            "OPENHANGAR_REPO_URL", "https://github.com/e2jk/OpenHangar"
        )
        _in_request = has_request_context()
        demo_display_id = None
        if is_demo and _in_request:
            slot_id = session.get("demo_slot_id")
            if slot_id:
                slot = db.session.get(DemoSlot, slot_id)
                if slot:
                    demo_display_id = slot.display_id
        role = current_user_role() if _in_request else None
        # Phase 23: is_pilot/is_maint also enabled by per-user capability flags
        uid = session.get("user_id") if _in_request else None
        _user_flags = db.session.get(User, uid) if uid else None
        _flag_pilot = bool(_user_flags and _user_flags.is_pilot)
        _flag_maint = bool(_user_flags and _user_flags.is_maintenance)

        # Phase 26: adaptive UI based on TenantProfile
        _tenant_profile = None
        if uid:
            tu = TenantUser.query.filter_by(user_id=uid).first()
            if tu:
                _tenant_profile = TenantProfile.query.filter_by(
                    tenant_id=tu.tenant_id
                ).first()
        _pac = (
            _tenant_profile.planned_aircraft_count
            if _tenant_profile and _tenant_profile.planned_aircraft_count is not None
            else None
        )
        # logbook_only: planned_aircraft_count == 0 → hide all aircraft UI
        _logbook_only = _pac == 0
        # single_aircraft_mode: planned_aircraft_count == 1 → hide fleet-level widgets
        _single_aircraft_mode = _pac == 1

        # EE-09: aviation history day banner
        from datetime import date as _date  # noqa: PLC0415
        from flask_babel import gettext as _gt, ngettext as _ngt  # noqa: PLC0415

        _today = _date.today()
        _avi_msgid = _aviation_day_msgid(_today.month, _today.day)
        _aviation_banner = _gt(_avi_msgid) if _avi_msgid else None

        # EE-10: personal anniversary banner (first solo / PPL)
        _pilot_anniversary: dict[str, Any] | None = None
        _pilot_anniversary_confetti = False
        if uid:
            from models import PilotProfile as _PP  # noqa: PLC0415

            _pp = _PP.query.filter_by(user_id=uid).first()
            if _pp:
                for _ann_date, _ann_type in (
                    (_pp.first_solo_date, "solo"),
                    (_pp.ppl_issue_date, "ppl"),
                ):
                    if _ann_date and (_ann_date.month, _ann_date.day) == (
                        _today.month,
                        _today.day,
                    ):
                        _years = _today.year - _ann_date.year
                        if _ann_type == "solo":
                            _msg = (
                                _ngt(
                                    "🎉 Today marks %(n)s year since your first solo flight!",
                                    "🎉 Today marks %(n)s years since your first solo flight!",
                                    _years,
                                    n=_years,
                                )
                                if _years > 0
                                else _gt(
                                    "🎉 Today is the anniversary of your first solo flight!"
                                )
                            )
                        else:
                            _msg = (
                                _ngt(
                                    "🎉 Today marks %(n)s year since you earned your PPL!",
                                    "🎉 Today marks %(n)s years since you earned your PPL!",
                                    _years,
                                    n=_years,
                                )
                                if _years > 0
                                else _gt("🎉 Today is the anniversary of your PPL!")
                            )
                        _pilot_anniversary = {
                            "type": _ann_type,
                            "years": _years,
                            "message": _msg,
                        }
                        _sess_key = f"anniversary_confetti_{_today.isoformat()}"
                        if not session.get(_sess_key):
                            session[_sess_key] = True
                            _pilot_anniversary_confetti = True
                        break

        _is_owner = role in (Role.ADMIN, Role.OWNER)
        _nav_update_available = (
            check_update_available() if _is_owner and _in_request else False
        )

        return {
            "logged_in": bool(uid),
            "has_users": User.query.count() > 0,
            "flask_env": flask_env,
            "is_demo": is_demo,
            "demo_next_wipe_utc": demo_next_wipe_utc,
            "demo_display_id": demo_display_id,
            "demo_site_url": demo_site_url,
            "repo_url": repo_url,
            "current_locale": str(_babel_get_locale()),
            "supported_locales": SUPPORTED_LOCALES,
            "locale_meta": LOCALE_META,
            "current_role": role,
            "is_owner": _is_owner,
            "is_pilot": role in (Role.ADMIN, Role.OWNER, Role.PILOT, Role.INSTRUCTOR)
            or _flag_pilot,
            "is_maint": role
            in (Role.ADMIN, Role.OWNER, Role.MAINTENANCE, Role.INSTRUCTOR)
            or _flag_maint,
            "is_crew": role not in (None, Role.VIEWER),
            "nav_user_label": (_user_flags.name or _user_flags.email)
            if _user_flags
            else None,
            "tenant_profile": _tenant_profile,
            "allows_rental": bool(_tenant_profile and _tenant_profile.allows_rental),
            "logbook_only": _logbook_only,
            "single_aircraft_mode": _single_aircraft_mode,
            "aircraft_count_goal": _pac,
            "aviation_day_banner": _aviation_banner,
            "pilot_anniversary": _pilot_anniversary,
            "pilot_anniversary_confetti": _pilot_anniversary_confetti,
            "today": _date.today(),
            "current_theme": _current_theme(_user_flags, _in_request, session, is_demo),
            "nav_update_available": _nav_update_available,
            "oh_debug": app.debug
            and os.environ.get("OPENHANGAR_SW_ENABLED", "").lower()
            not in ("1", "true", "yes"),
        }

    @app.errorhandler(403)
    def forbidden(e: Exception) -> ResponseReturnValue:
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e: Exception) -> ResponseReturnValue:
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def internal_error(e: Exception) -> ResponseReturnValue:
        import traceback as _tb

        show_debug = flask_env in ("development", "test")
        exc_type = type(e).__name__
        exc_value = str(e)
        tb = _tb.format_exc() if show_debug else None
        return (
            render_template(
                "errors/500.html",
                show_debug=show_debug,
                env=flask_env,
                exc_type=exc_type,
                exc_value=exc_value,
                tb=tb,
            ),
            500,
        )

    @app.route("/")
    def index() -> ResponseReturnValue:
        from models import TenantUser, User

        # Demo mode: unauthenticated visitors always see the landing page
        if flask_env == "demo" and not session.get("user_id"):
            return render_template("landing.html")

        if User.query.count() == 0:
            return render_template("landing.html")
        if session.get("user_id"):
            from datetime import date as _date
            from models import Aircraft, FlightEntry, MaintenanceTrigger, Snag
            from utils import accessible_aircraft, compute_aircraft_statuses

            tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
            aircraft = accessible_aircraft(tu.tenant_id).all() if tu else []
            aircraft_ids = [ac.id for ac in aircraft]
            hobbs_by_aircraft = Aircraft.engine_hours_by_id(aircraft_ids)

            recent_flights = (
                (
                    FlightEntry.query.filter(FlightEntry.aircraft_id.in_(aircraft_ids))
                    .order_by(FlightEntry.date.desc(), FlightEntry.id.desc())
                    .limit(5)
                    .all()
                )
                if aircraft_ids
                else []
            )

            today = _date.today()
            month_start = today.replace(day=1)
            month_flights = (
                (
                    FlightEntry.query.filter(
                        FlightEntry.aircraft_id.in_(aircraft_ids),
                        FlightEntry.date >= month_start,
                    ).all()
                )
                if aircraft_ids
                else []
            )
            hours_this_month = sum(
                float(f.flight_time)
                if f.flight_time is not None
                else float(f.flight_time_counter_end)
                - float(f.flight_time_counter_start)
                for f in month_flights
                if f.flight_time is not None
                or (
                    f.flight_time_counter_end is not None
                    and f.flight_time_counter_start is not None
                )
            )
            flights_this_month = len(month_flights)

            triggers = (
                (
                    MaintenanceTrigger.query.filter(
                        MaintenanceTrigger.aircraft_id.in_(aircraft_ids)
                    ).all()
                )
                if aircraft_ids
                else []
            )

            aircraft_status = compute_aircraft_statuses(
                aircraft, triggers, hobbs_by_aircraft
            )

            urgent_maintenance = []
            maintenance_alerts = 0
            ac_by_id = {ac.id: ac for ac in aircraft}
            for t in triggers:
                s = t.status(hobbs_by_aircraft.get(t.aircraft_id))
                if s in ("overdue", "due_soon"):
                    maintenance_alerts += 1
                    urgent_maintenance.append((t, s, ac_by_id[t.aircraft_id]))
            urgent_maintenance.sort(key=lambda x: 0 if x[1] == "overdue" else 1)
            urgent_maintenance = urgent_maintenance[:5]

            # Collect grounding snags across the fleet (sorted: grounding first, then by date)
            open_grounding = (
                (
                    Snag.query.filter(
                        Snag.aircraft_id.in_(aircraft_ids),
                        Snag.is_grounding.is_(True),
                        Snag.resolved_at.is_(None),
                    )
                    .order_by(Snag.reported_at.desc())
                    .all()
                )
                if aircraft_ids
                else []
            )
            grounding_snags = [(s, ac_by_id[s.aircraft_id]) for s in open_grounding]

            from models import PilotLogbookEntry, PilotProfile
            from pilots.currency import currency_summary as _currency_summary

            pilot_profile = PilotProfile.query.filter_by(
                user_id=session["user_id"]
            ).first()
            pilot_entries = (
                PilotLogbookEntry.query.filter_by(
                    pilot_user_id=session["user_id"]
                ).all()
                if pilot_profile
                else []
            )
            pilot_currency = _currency_summary(pilot_profile, pilot_entries, today)

            recent_pilot_entries = (
                sorted(pilot_entries, key=lambda e: (e.date, e.id), reverse=True)[:5]
                if not aircraft_ids
                else []
            )

            from flask import url_for as _url_for_dash

            track_entries = (
                PilotLogbookEntry.query.filter_by(pilot_user_id=session["user_id"])
                .filter(PilotLogbookEntry.gps_track_id.isnot(None))
                .order_by(PilotLogbookEntry.date.asc())
                .all()
                if pilot_profile
                else []
            )
            dash_track_rows = [
                {
                    "date": str(e.date),
                    "dep": e.departure_place or "",
                    "arr": e.arrival_place or "",
                    "time_str": f"{e.total_flight_time} h"
                    if e.total_flight_time is not None
                    else "",
                    "view_url": _url_for_dash(
                        "aircraft.flight_detail",
                        aircraft_id=e.flight.aircraft_id,
                        flight_id=e.flight_id,
                    )
                    if e.flight_id and e.flight
                    else _url_for_dash("pilots.view_entry", entry_id=e.id),
                    "geojson": e.gps_track.geojson if e.gps_track else None,
                }
                for e in track_entries
            ]
            from models import AppSetting as _AppSetting

            _openaip_s = db.session.get(_AppSetting, "openaip_api_key")
            openaip_key = _openaip_s.value if _openaip_s and _openaip_s.value else None

            # ── Reservation stat card + pending approval queue ────────────────
            import calendar as _cal
            from collections import defaultdict
            from datetime import datetime as _dt, timedelta, timezone as _tz
            from models import Reservation, ReservationStatus

            from models import Role
            from utils import current_user_role

            _role = current_user_role()
            today_utc = _dt.now(_tz.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            pending_reservations = (
                (
                    Reservation.query.filter(
                        Reservation.aircraft_id.in_(aircraft_ids),
                        Reservation.status == ReservationStatus.PENDING,
                        Reservation.start_dt >= today_utc,
                    )
                    .order_by(Reservation.start_dt)
                    .all()
                )
                if aircraft_ids and _role in (Role.ADMIN, Role.OWNER)
                else []
            )
            res_7d = (
                Reservation.query.filter(
                    Reservation.aircraft_id.in_(aircraft_ids),
                    Reservation.start_dt >= today_utc,
                    Reservation.start_dt < today_utc + timedelta(days=7),
                ).count()
                if aircraft_ids
                else 0
            )
            res_30d = (
                Reservation.query.filter(
                    Reservation.aircraft_id.in_(aircraft_ids),
                    Reservation.start_dt >= today_utc,
                    Reservation.start_dt < today_utc + timedelta(days=30),
                ).count()
                if aircraft_ids
                else 0
            )

            # ── Fleet calendar widget ─────────────────────────────────────────
            try:
                cal_year = int(request.args.get("cal_year", today.year))
                cal_month = int(request.args.get("cal_month", today.month))
            except ValueError:
                cal_year, cal_month = today.year, today.month
            if cal_month < 1:
                cal_year -= 1
                cal_month = 12
            if cal_month > 12:
                cal_year += 1
                cal_month = 1

            cal_month_start = _dt(cal_year, cal_month, 1, tzinfo=_tz.utc)
            cal_last_day = _cal.monthrange(cal_year, cal_month)[1]
            cal_month_end = _dt(
                cal_year, cal_month, cal_last_day, 23, 59, 59, tzinfo=_tz.utc
            )

            cal_reservations = (
                Reservation.query.filter(
                    Reservation.aircraft_id.in_(aircraft_ids),
                    Reservation.status != ReservationStatus.CANCELLED,
                    Reservation.start_dt <= cal_month_end,
                    Reservation.end_dt >= cal_month_start,
                )
                .order_by(Reservation.start_dt)
                .all()
                if aircraft_ids
                else []
            )

            cal_flights = (
                FlightEntry.query.filter(
                    FlightEntry.aircraft_id.in_(aircraft_ids),
                    FlightEntry.date >= cal_month_start.date(),
                    FlightEntry.date <= cal_month_end.date(),
                )
                .order_by(FlightEntry.date)
                .all()
                if aircraft_ids
                else []
            )

            cal_day_events: dict[Any, Any] = defaultdict(
                lambda: {"reservations": [], "flights": []}
            )
            for r in cal_reservations:
                cur = r.start_dt.date()
                end = r.end_dt.date()
                while cur <= end:
                    if cur.month == cal_month and cur.year == cal_year:
                        cal_day_events[cur]["reservations"].append(r)
                    cur += timedelta(days=1)
            for f in cal_flights:
                cal_day_events[f.date]["flights"].append(f)

            cal_weeks = _cal.Calendar(firstweekday=0).monthdatescalendar(
                cal_year, cal_month
            )
            cal_prev_month = cal_month - 1 or 12
            cal_prev_year = cal_year - 1 if cal_month == 1 else cal_year
            cal_next_month = cal_month % 12 + 1
            cal_next_year = cal_year + 1 if cal_month == 12 else cal_year
            cal_month_name = _dt(cal_year, cal_month, 1).strftime("%B %Y")

            return render_template(
                "dashboard.html",
                aircraft=aircraft,
                pending_reservations=pending_reservations,
                recent_flights=recent_flights,
                recent_pilot_entries=recent_pilot_entries,
                dash_track_rows=dash_track_rows,
                openaip_key=openaip_key,
                hours_this_month=hours_this_month,
                flights_this_month=flights_this_month,
                maintenance_alerts=maintenance_alerts,
                urgent_maintenance=urgent_maintenance,
                grounding_snags=grounding_snags,
                aircraft_status=aircraft_status,
                triggers=triggers,
                pilot_currency=pilot_currency,
                today=today,
                res_7d=res_7d,
                res_30d=res_30d,
                cal_weeks=cal_weeks,
                cal_day_events=cal_day_events,
                cal_month_name=cal_month_name,
                cal_year=cal_year,
                cal_month=cal_month,
                cal_prev_year=cal_prev_year,
                cal_prev_month=cal_prev_month,
                cal_next_year=cal_next_year,
                cal_next_month=cal_next_month,
                ReservationStatus=ReservationStatus,
            )
        return render_template("welcome.html")

    @app.route("/not-yet-implemented")
    def not_yet_implemented() -> ResponseReturnValue:
        feature = request.args.get("feature", "This feature")
        return render_template("not_yet_implemented.html", feature=feature), 501

    @app.route("/set-language/<lang>")
    def set_language(lang: str) -> ResponseReturnValue:
        from flask import abort, redirect

        if lang not in SUPPORTED_LOCALES:
            abort(400)
        if session.get("user_id") and not session.get("demo_slot_id"):
            from models import User

            user = db.session.get(User, session["user_id"])
            if user:
                user.language = lang
                db.session.commit()
            else:
                session["language"] = (
                    lang  # stale user_id (e.g. setup wizard) — fall back to session
                )
        else:
            session["language"] = lang
        next_url = request.args.get("next", "").strip()
        next_url = next_url.replace(
            "\\", ""
        )  # browsers treat \ as /; strip before parsing
        parsed_next = urlparse(next_url)
        if (
            not next_url
            or parsed_next.netloc
            or parsed_next.scheme
            or not next_url.startswith("/")
            or next_url.startswith("//")
        ):
            next_url = "/"
        return redirect(next_url)

    @app.route("/set-theme/<theme>")
    def set_theme(theme: str) -> ResponseReturnValue:
        from flask import abort, redirect

        if theme not in ("light", "dark", "system"):
            abort(400)
        if session.get("user_id") and not session.get("demo_slot_id"):
            from models import User

            user = db.session.get(User, session["user_id"])
            if user:
                user.theme = None if theme == "system" else theme
                db.session.commit()
            else:
                session["theme"] = theme
        else:
            session["theme"] = theme
        next_url = request.args.get("next", "").strip()
        next_url = next_url.replace("\\", "")
        parsed_next = urlparse(next_url)
        if (
            not next_url
            or parsed_next.netloc
            or parsed_next.scheme
            or not next_url.startswith("/")
            or next_url.startswith("//")
        ):
            next_url = "/"
        return redirect(next_url)

    @app.route("/robots.txt")
    def robots_txt() -> ResponseReturnValue:
        return send_from_directory(
            app.static_folder or "static", "robots.txt", mimetype="text/plain"
        )

    @app.route("/favicon.ico")
    def favicon() -> ResponseReturnValue:
        return send_from_directory(
            app.static_folder or "static", "favicon.svg", mimetype="image/svg+xml"
        )

    @app.route("/health")
    def health() -> ResponseReturnValue:
        # Liveness probe: proves the worker is up and routing. Deliberately does
        # NOT touch the database — a liveness check must not fail (and trigger a
        # restart) just because a dependency is down. See /health/ready below.
        return {"status": "ok"}, 200

    @app.route("/health/ready")
    def health_ready() -> ResponseReturnValue:
        # Readiness probe: confirms the database is reachable. Reserved for the
        # in-container Docker healthcheck (curl localhost:5000); public callers
        # arrive via Traefik with a non-loopback remote_addr (ProxyFix x_for=1),
        # so they get a 404 and the endpoint stays hidden and unabusable. The
        # check itself is a single cheap "SELECT 1".
        from flask import abort as _abort
        from sqlalchemy import text as _text
        from sqlalchemy.exc import SQLAlchemyError

        if request.remote_addr not in ("127.0.0.1", "::1"):
            _abort(404)
        try:
            db.session.execute(_text("SELECT 1"))
        except SQLAlchemyError:
            db.session.rollback()
            return {"status": "degraded", "database": "down"}, 503
        return {"status": "ready"}, 200

    @app.cli.command("check-empty-db")
    def check_empty_db_command() -> None:
        """Exit 0 if the database has no user data, 1 if it does (restore safety check)."""
        import sys

        from sqlalchemy.exc import ProgrammingError  # pyright: ignore[reportMissingImports]

        from models import User  # pyright: ignore[reportMissingImports]

        try:
            count = User.query.count()
        except ProgrammingError:
            # Schema not initialised (table missing) — treat as empty.
            print("Database is empty.")
            return
        if count == 0:
            print("Database is empty.")
        else:
            print(f"Database has {count} user(s) — not empty.", file=sys.stderr)
            sys.exit(1)

    @app.cli.command("restore-backup")
    @click.argument("archive_path")
    def restore_backup_command(archive_path: str) -> None:
        """Restore a backup archive into the current empty database."""
        import io
        import json
        import sys
        import zipfile

        from flask import current_app  # pyright: ignore[reportMissingImports]

        from models import User  # pyright: ignore[reportMissingImports]

        # ── safety: refuse if DB already has data ─────────────────────────────
        if User.query.count() > 0:
            print(
                "ERROR: Database is not empty. Restore refused to prevent data loss.",
                file=sys.stderr,
            )
            sys.exit(1)

        # ── decrypt + extract ─────────────────────────────────────────────────
        with open(archive_path, "rb") as fh:
            payload = fh.read()

        # Use only OPENHANGAR_RESTORE_ENCRYPTION_KEY for decryption — never fall
        # back to OPENHANGAR_BACKUP_ENCRYPTION_KEY, which may be set to a
        # different key (e.g. the dev backup key) and would silently fail with a
        # wrong-key decryption error instead of prompting for the correct key.
        encryption_key_raw = os.environ.get("OPENHANGAR_RESTORE_ENCRYPTION_KEY", "")
        if encryption_key_raw:
            from config.routes import _derive_key  # pyright: ignore[reportMissingImports]
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # pyright: ignore[reportMissingImports]

            key = _derive_key(encryption_key_raw)
            nonce, ct = payload[:12], payload[12:]
            try:
                zip_bytes = AESGCM(key).decrypt(nonce, ct, None)
            except Exception as exc:
                print(f"ERROR: Decryption failed — wrong key? ({exc})", file=sys.stderr)
                sys.exit(1)
        else:
            if str(archive_path).endswith(".enc"):
                print(
                    "ERROR: Archive is encrypted (.enc) but no decryption key is available.\n"
                    "       Set OPENHANGAR_RESTORE_ENCRYPTION_KEY (recommended for cross-\n"
                    "       environment restores) or use the restore script's --key-file\n"
                    "       option or interactive prompt.",
                    file=sys.stderr,
                )
                sys.exit(1)
            zip_bytes = payload

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            metadata: dict[str, str] = (
                json.loads(zf.read("metadata.json")) if "metadata.json" in names else {}
            )
            sql_bytes = zf.read("openhangar.sql")
            upload_entries = [n for n in names if n.startswith("uploads/")]

        # ── version check ─────────────────────────────────────────────────────
        backup_alembic = metadata.get("alembic_head") or "unknown"
        backup_version = metadata.get("app_version", "unknown")
        current_version = os.environ.get("OPENHANGAR_VERSION", "development")
        print(f"Backup:  version={backup_version}  alembic={backup_alembic}")
        print(f"Current: version={current_version}")

        if backup_alembic != "unknown":
            try:
                from alembic.script import ScriptDirectory  # pyright: ignore[reportMissingImports]
                from flask_migrate import Migrate as _Migrate  # pyright: ignore[reportMissingImports]

                _m = _Migrate(current_app, db)
                scripts = ScriptDirectory.from_config(_m.get_config())
                known = {s.revision for s in scripts.walk_revisions()}
                if backup_alembic not in known:
                    print(
                        f"ERROR: Backup Alembic revision '{backup_alembic}' is not in "
                        "this container's migration chain. Restore a container version "
                        "that knows this migration.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
            except Exception as exc:
                print(f"WARNING: Could not verify Alembic compatibility: {exc}")

        # ── drop schema + restore SQL dump ────────────────────────────────────
        database_url = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
        if not database_url.startswith("postgresql"):
            print(
                f"ERROR: Only PostgreSQL is supported for restore (got: {database_url!r})",
                file=sys.stderr,
            )
            sys.exit(1)

        print(
            "Dropping existing schema and restoring from backup (this may take a minute)..."
        )
        try:
            _drop_and_restore_schema(database_url, sql_bytes)
        except RuntimeError as exc:
            print(f"ERROR: psql restore failed:\n{exc}", file=sys.stderr)
            sys.exit(1)

        # ── restore uploaded files ────────────────────────────────────────────
        # The DB has just been replaced, so any files already on disk are now
        # orphaned.  Before clearing, snapshot them into a dated zip in the
        # backup folder so nothing is silently destroyed.
        import shutil as _shutil

        upload_folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
        backup_folder = current_app.config.get("BACKUP_FOLDER", "/data/backups")
        os.makedirs(upload_folder, exist_ok=True)
        os.makedirs(backup_folder, exist_ok=True)

        _existing = [
            (dp, f) for dp, _dirs, files in os.walk(upload_folder) for f in files
        ]
        if _existing:
            from datetime import datetime, timezone as _tz

            _snap_ts = datetime.now(_tz.utc).strftime("%Y%m%dT%H%M%SZ")
            _enc_key_raw = os.environ.get("OPENHANGAR_BACKUP_ENCRYPTION_KEY", "")
            _snap_ext = ".zip.enc" if _enc_key_raw else ".zip"
            _snap_name = f"uploads_pre_restore_{_snap_ts}{_snap_ext}"
            _snap_path = os.path.join(backup_folder, _snap_name)

            _snap_buf = io.BytesIO()
            with zipfile.ZipFile(_snap_buf, "w", zipfile.ZIP_DEFLATED) as _zf:
                for _dp, _fn in _existing:
                    _fp = os.path.join(_dp, _fn)
                    _rel = os.path.relpath(_fp, upload_folder)
                    _zf.write(_fp, arcname=_rel)
            _snap_bytes = _snap_buf.getvalue()

            if _enc_key_raw:
                from config.routes import _derive_key, _encrypt_bytes  # pyright: ignore[reportMissingImports]

                _snap_bytes = _encrypt_bytes(_snap_bytes, _derive_key(_enc_key_raw))

            with open(_snap_path, "wb") as _fh:
                _fh.write(_snap_bytes)

            print(
                f"WARNING: {len(_existing)} pre-existing file(s) found in the upload folder.\n"
                f"         They have been snapshotted to:\n"
                f"         {_snap_path}\n"
                f"         {'(encrypted with OPENHANGAR_BACKUP_ENCRYPTION_KEY) ' if _enc_key_raw else ''}"
                f"The upload folder will now be cleared."
            )

        for _item in os.scandir(upload_folder):
            if _item.is_dir():
                _shutil.rmtree(_item.path, ignore_errors=True)
            else:
                os.unlink(_item.path)

        if upload_entries:
            upload_root = os.path.realpath(upload_folder)
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for entry in upload_entries:
                    # entry is e.g. "uploads/tenant/OO-REG/photos/01-abc.jpg"
                    rel = entry[len("uploads/") :]
                    if not rel:
                        continue  # skip the bare "uploads/" directory entry
                    dest = os.path.join(upload_folder, rel)
                    dest_real = os.path.realpath(dest)
                    if dest_real != upload_root and not dest_real.startswith(
                        upload_root + os.sep
                    ):
                        print(
                            f"ERROR: Archive entry {entry!r} resolves outside the "
                            "upload folder — refusing to restore (corrupted or "
                            "tampered archive).",
                            file=sys.stderr,
                        )
                        sys.exit(1)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, "wb") as fh:
                        fh.write(zf.read(entry))
            print(f"Restored {len(upload_entries)} uploaded file(s).")
        else:
            print("Backup contains no uploaded files; upload folder cleared.")

        print("Restore complete.")

    @app.cli.command("backup-now")
    def backup_now_command() -> None:
        import sys

        from config.routes import run_backup

        try:
            record = run_backup()
            print(
                f"Backup OK: {record.filename} ({record.size_bytes} bytes, sha256={record.sha256})"
            )
        except RuntimeError as exc:
            print(f"Backup FAILED: {exc}")
            sys.exit(1)

    # Flask CLI command used by demo/refresh.sh to drop and recreate the schema.
    # Only works in demo mode — production uses Alembic migrations.
    @app.cli.command("reset-db")
    def reset_db_command() -> None:
        if flask_env != "demo":
            print("reset-db is only available in demo mode. Aborting.")
            return
        db.drop_all()
        db.create_all()
        print("Database schema reset.")

    # Flask CLI command used by demo/refresh.sh to wipe and reseed demo slots
    @app.cli.command("seed-demo")
    def seed_demo_command() -> None:
        from demo_seed import seed as demo_seed

        demo_seed()
        print("Demo slots reseeded.")

        # Re-apply env-var settings wiped by reset-db
        openaip_key = os.environ.get("OPENHANGAR_OPENAIP_API_KEY", "").strip()
        if openaip_key:
            from models import AppSetting

            setting = db.session.get(AppSetting, "openaip_api_key")
            if setting is None:
                db.session.add(AppSetting(key="openaip_api_key", value=openaip_key))
            else:
                setting.value = openaip_key
            db.session.commit()
            print("Environment settings applied.")

    # Only run against a real PostgreSQL database (sqlite = dev/test), and only
    # when called from a long-running server process — not from init scripts such
    # as docker-init-db.py that run migrations before the schema exists.
    if "sqlite" not in app.config.get(
        "SQLALCHEMY_DATABASE_URI", ""
    ) and not os.environ.get("OPENHANGAR_SKIP_BACKGROUND_THREADS"):
        from services.version_service import start_version_check_thread  # pyright: ignore[reportMissingImports]

        start_version_check_thread(app)
        from sync_watcher import start_sync_watcher  # pyright: ignore[reportMissingImports]

        start_sync_watcher(app)
        if os.environ.get("OPENHANGAR_ENV", "production") == "production":
            _start_easa_sync_scheduler(app)
            _start_notification_scheduler(app)
            import threading
            from services.notification_service import send_welcome_email_if_needed  # pyright: ignore[reportMissingImports]

            threading.Thread(
                target=send_welcome_email_if_needed,
                args=(app,),
                daemon=True,
                name="welcome-email",
            ).start()

    if (
        os.environ.get("WERKZEUG_RUN_MAIN") == "true"
        and os.environ.get("OPENHANGAR_ENV", "production") == "development"
        and os.environ.get("OPENHANGAR_SW_ENABLED", "").lower() in ("1", "true", "yes")
    ):
        print("OPENHANGAR_SW_ENABLED: service worker active in debug mode", flush=True)

    _validate_config(app)
    return app


def _validate_config(app: Flask) -> None:
    """Collect and report all configuration problems at once rather than one at a time."""
    errors: list[str] = []

    # OPENHANGAR_SECRET_KEY: minimum length (existence and placeholder already checked above)
    secret = app.config.get("SECRET_KEY", "")
    if secret and len(secret) < 32:
        errors.append(
            f"OPENHANGAR_SECRET_KEY is too short ({len(secret)} chars, minimum 32). "
            "Generate one with: openssl rand -hex 32"
        )

    # OPENHANGAR_ENV: must be one of the known values when set
    _raw_env = os.environ.get("OPENHANGAR_ENV", "")
    if _raw_env and _raw_env not in ("production", "development", "test", "demo"):
        errors.append(
            f"OPENHANGAR_ENV must be one of: production, development, test, demo "
            f"(got {_raw_env!r})"
        )

    # OPENHANGAR_MAX_UPLOAD_BYTES: must be a plain positive integer when set
    _raw_max = os.environ.get("OPENHANGAR_MAX_UPLOAD_BYTES", "")
    _validated_max: int | None = None
    if _raw_max:
        try:
            _parsed = int(_raw_max)
            if _parsed <= 0:
                errors.append("OPENHANGAR_MAX_UPLOAD_BYTES must be a positive integer")
            else:
                _validated_max = _parsed
        except ValueError:
            errors.append(
                f"OPENHANGAR_MAX_UPLOAD_BYTES must be a plain integer (bytes), got {_raw_max!r}. "
                "Example: 52428800 for 50 MB."
            )

    # OPENHANGAR_SYNC_SCAN_INTERVAL: must be a positive integer when set
    _raw_interval = os.environ.get("OPENHANGAR_SYNC_SCAN_INTERVAL", "")
    if _raw_interval:
        try:
            _parsed_interval = int(_raw_interval)
            if _parsed_interval <= 0:
                errors.append(
                    "OPENHANGAR_SYNC_SCAN_INTERVAL must be a positive integer (seconds)"
                )
        except ValueError:
            errors.append(
                f"OPENHANGAR_SYNC_SCAN_INTERVAL must be a plain integer (seconds), got {_raw_interval!r}. "
                "Example: 60"
            )

    # OPENHANGAR_DATABASE_URL: production deployments must use PostgreSQL
    db_url = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    flask_env = os.environ.get("OPENHANGAR_ENV", "production")
    if "sqlite" not in db_url and flask_env not in ("development", "test"):
        if not db_url.startswith(("postgresql://", "postgresql+psycopg2://")):
            scheme = db_url.split("://")[0] if "://" in db_url else db_url[:20]
            errors.append(
                f"OPENHANGAR_DATABASE_URL scheme {scheme!r} is not supported in production. "
                "Use 'postgresql://' or 'postgresql+psycopg2://'."
            )

    # OPENHANGAR_BACKUP_ENCRYPTION_KEY / OPENHANGAR_RESTORE_ENCRYPTION_KEY:
    # whitespace-only values are likely a misconfiguration.
    enc_key = os.environ.get("OPENHANGAR_BACKUP_ENCRYPTION_KEY", "")
    if enc_key and not enc_key.strip():
        errors.append(
            "OPENHANGAR_BACKUP_ENCRYPTION_KEY is set but contains only whitespace. "
            "Either provide a real key or leave the variable unset."
        )
    restore_enc_key = os.environ.get("OPENHANGAR_RESTORE_ENCRYPTION_KEY", "")
    if restore_enc_key and not restore_enc_key.strip():
        errors.append(
            "OPENHANGAR_RESTORE_ENCRYPTION_KEY is set but contains only whitespace. "
            "Either provide a real key or leave the variable unset."
        )

    # OPENHANGAR_SMTP_PORT: must be an integer in valid port range when set
    _raw_smtp_port = os.environ.get("OPENHANGAR_SMTP_PORT", "")
    if _raw_smtp_port:
        try:
            _smtp_port_val = int(_raw_smtp_port)
            if not (1 <= _smtp_port_val <= 65535):
                errors.append(
                    f"OPENHANGAR_SMTP_PORT must be between 1 and 65535, got {_raw_smtp_port!r}"
                )
        except ValueError:
            errors.append(
                f"OPENHANGAR_SMTP_PORT must be an integer, got {_raw_smtp_port!r}"
            )

    # OPENHANGAR_DEMO_BUSY_WINDOW_MINUTES: must be a positive integer when set
    _raw_busy = os.environ.get("OPENHANGAR_DEMO_BUSY_WINDOW_MINUTES", "")
    if _raw_busy:
        try:
            if int(_raw_busy) <= 0:
                errors.append(
                    "OPENHANGAR_DEMO_BUSY_WINDOW_MINUTES must be a positive integer"
                )
        except ValueError:
            errors.append(
                f"OPENHANGAR_DEMO_BUSY_WINDOW_MINUTES must be a plain integer (minutes), "
                f"got {_raw_busy!r}"
            )

    # OPENHANGAR_NOTIFICATION_TIME: optional, but must be valid HH:MM when set
    _raw_notif_time = os.environ.get("OPENHANGAR_NOTIFICATION_TIME", "")
    if _raw_notif_time:
        try:
            _parse_notification_time()
        except ValueError as exc:
            errors.append(str(exc))

    # OPENHANGAR_ALERT_NTFY_TOPIC_URL: must be an http(s) URL when set
    _ntfy_url = os.environ.get("OPENHANGAR_ALERT_NTFY_TOPIC_URL", "").strip()
    if _ntfy_url and not _ntfy_url.startswith(("http://", "https://")):
        errors.append(
            f"OPENHANGAR_ALERT_NTFY_TOPIC_URL must start with http:// or https://, "
            f"got {_ntfy_url!r}"
        )

    # OPENHANGAR_ALERT_EMAIL_TO: must look like an email address when set,
    # and SMTP must be configured for delivery to be possible
    _alert_email = os.environ.get("OPENHANGAR_ALERT_EMAIL_TO", "").strip()
    if _alert_email:
        if "@" not in _alert_email:
            errors.append(
                f"OPENHANGAR_ALERT_EMAIL_TO must be a valid email address, "
                f"got {_alert_email!r}"
            )
        elif not os.environ.get("OPENHANGAR_SMTP_HOST", "").strip():
            errors.append(
                "OPENHANGAR_ALERT_EMAIL_TO is set but OPENHANGAR_SMTP_HOST is not configured — "
                "alert emails cannot be delivered"
            )

    # OPENHANGAR_ALERT_WEBHOOK_URL: must be an http(s) URL when set
    _webhook_url = os.environ.get("OPENHANGAR_ALERT_WEBHOOK_URL", "").strip()
    if _webhook_url and not _webhook_url.startswith(("http://", "https://")):
        errors.append(
            f"OPENHANGAR_ALERT_WEBHOOK_URL must start with http:// or https://, "
            f"got {_webhook_url!r}"
        )

    if errors:
        bullet_list = "\n".join(f"  • {e}" for e in errors)
        raise RuntimeError(
            f"Configuration errors — fix before starting:\n{bullet_list}"
        )

    if _validated_max is not None:
        app.config["MAX_CONTENT_LENGTH"] = _validated_max


if __name__ == "__main__":  # pragma: no cover
    _debug = os.environ.get("OPENHANGAR_ENV") == "development"
    create_app().run(host="0.0.0.0", port=5000, debug=_debug)  # nosec B104
