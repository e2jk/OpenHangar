import os
import sqlite3
from datetime import timedelta

import click  # pyright: ignore[reportMissingImports]
from typing import Any
from urllib.parse import urlparse

from flask import Flask, render_template, request, send_from_directory, session  # pyright: ignore[reportMissingImports]
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


def _fetch_latest_version() -> str | None:
    """Query GitHub Releases API for the latest published tag. Returns bare version or None."""
    import json
    import urllib.request

    req = urllib.request.Request(
        "https://api.github.com/repos/e2jk/OpenHangar/releases/latest",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "OpenHangar-version-check",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
            data = json.loads(resp.read())
            tag = data.get("tag_name", "")
            return tag.lstrip("v") or None
    except Exception:
        return None


def _upsert_app_setting(db_session: Any, key: str, value: str) -> None:
    from models import AppSetting

    setting = db_session.get(AppSetting, key)
    if setting:
        setting.value = value
    else:
        db_session.add(AppSetting(key=key, value=value))


def _run_version_check(app: Flask) -> None:
    """Check GitHub for the latest release and cache result in AppSetting."""
    from datetime import datetime, timedelta, timezone

    from models import AppSetting, db

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

        latest = _fetch_latest_version()
        _upsert_app_setting(
            db.session,
            "version_last_checked_at",
            datetime.now(timezone.utc).isoformat(),
        )
        if latest:
            _upsert_app_setting(db.session, "latest_version", latest)
        db.session.commit()


def _version_check_loop(app: Flask, _sleep_fn: Any = None) -> None:
    """Daemon thread body — random startup delay then every 24 h."""
    import random
    import time as _time

    sleep = _sleep_fn if _sleep_fn is not None else _time.sleep
    sleep(random.randint(0, 6 * 3600))
    while True:
        try:
            _run_version_check(app)
        except Exception:
            app.logger.exception("Version check failed; will retry in 24 h")
        sleep(24 * 3600)


def _start_version_check_thread(app: Flask) -> None:
    import threading

    t = threading.Thread(
        target=_version_check_loop,
        args=(app,),
        daemon=True,
        name="version-check",
    )
    t.start()


def create_app() -> Flask:
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]

    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", "sqlite:///:memory:"
    )
    secret_key = os.environ.get("SECRET_KEY")
    if not secret_key:
        raise RuntimeError("SECRET_KEY environment variable must be set")
    if "change" in secret_key.lower():
        raise RuntimeError(
            "SECRET_KEY appears to be a placeholder value. "
            "Generate a real key with: openssl rand -hex 32"
        )
    app.config["SECRET_KEY"] = secret_key
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["UPLOAD_FOLDER"] = os.environ.get("UPLOAD_FOLDER", "/data/uploads")
    app.config["BACKUP_FOLDER"] = os.environ.get("BACKUP_FOLDER", "/data/backups")
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)

    flask_env = os.environ.get("FLASK_ENV", "production")

    if flask_env in ("development", "test"):
        app.config["TEMPLATES_AUTO_RELOAD"] = True

    from models import db

    db.init_app(app)
    Migrate(app, db)

    def _get_locale() -> str | None:
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

    from extensions import limiter as _limiter  # pyright: ignore[reportMissingImports]

    _limiter.init_app(app)

    @app.after_request
    def _security_headers(response: Any) -> Any:
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    from flask_babel import format_date, format_datetime, format_decimal

    app.jinja_env.globals.update(
        format_date=format_date,
        format_datetime=format_datetime,
        format_decimal=format_decimal,
    )

    from utils import (
        _load_aircraft_type_variants,
        _load_airport_names,
    )

    @app.template_filter("airport_name")
    def _airport_name_filter(code: str | None) -> str:
        if not code:
            return ""
        return _load_airport_names().get(code.upper(), "")

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
        q_low = q.lower()
        variants = _load_aircraft_type_variants()
        code_hits: list[dict[str, str]] = []
        name_hits: list[dict[str, str]] = []
        for des, full_name in variants:
            if des.startswith(q_up):
                code_hits.append({"code": des, "name": full_name})
            elif q_low in full_name.lower():
                name_hits.append({"code": des, "name": full_name})
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

    if flask_env == "demo":
        from demo.routes import demo_bp

        app.register_blueprint(demo_bp)

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        from models import DemoSlot, Role, TenantProfile, TenantUser, User
        from utils import current_user_role

        is_demo = flask_env == "demo"
        demo_next_wipe_utc = os.environ.get("DEMO_NEXT_WIPE_UTC") if is_demo else None
        demo_site_url = os.environ.get("DEMO_SITE_URL")
        repo_url = os.environ.get("REPO_URL", "https://github.com/e2jk/OpenHangar")
        demo_display_id = None
        if is_demo:
            slot_id = session.get("demo_slot_id")
            if slot_id:
                slot = db.session.get(DemoSlot, slot_id)
                if slot:
                    demo_display_id = slot.display_id
        role = current_user_role()
        # Phase 23: is_pilot/is_maint also enabled by per-user capability flags
        uid = session.get("user_id")
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
        from flask_babel import gettext as _gt  # noqa: PLC0415

        _today = _date.today()
        _avi_msgid = _aviation_day_msgid(_today.month, _today.day)
        _aviation_banner = _gt(_avi_msgid) if _avi_msgid else None

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
            "is_owner": role in (Role.ADMIN, Role.OWNER),
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
            "logbook_only": _logbook_only,
            "single_aircraft_mode": _single_aircraft_mode,
            "aircraft_count_goal": _pac,
            "aviation_day_banner": _aviation_banner,
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
            from models import FlightEntry, MaintenanceTrigger, Snag
            from utils import accessible_aircraft, compute_aircraft_statuses

            tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
            aircraft = accessible_aircraft(tu.tenant_id).all() if tu else []
            aircraft_ids = [ac.id for ac in aircraft]
            hobbs_by_aircraft = {ac.id: ac.total_engine_hours for ac in aircraft}

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
                hours_this_month=hours_this_month,
                flights_this_month=flights_this_month,
                maintenance_alerts=maintenance_alerts,
                urgent_maintenance=urgent_maintenance,
                grounding_snags=grounding_snags,
                aircraft_status=aircraft_status,
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

    @app.route("/favicon.ico")
    def favicon() -> ResponseReturnValue:
        return send_from_directory(
            app.static_folder or "static", "favicon.svg", mimetype="image/svg+xml"
        )

    @app.route("/health")
    def health() -> ResponseReturnValue:
        return {"status": "ok"}, 200

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

        encryption_key_raw = os.environ.get("BACKUP_ENCRYPTION_KEY", "")
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
        if upload_entries:
            upload_folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
            os.makedirs(upload_folder, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for entry in upload_entries:
                    fname = os.path.basename(entry)
                    if fname:
                        with open(os.path.join(upload_folder, fname), "wb") as fh:
                            fh.write(zf.read(entry))
            print(f"Restored {len(upload_entries)} uploaded file(s).")

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

    # Only run against a real PostgreSQL database (sqlite = dev/test).
    if "sqlite" not in app.config.get("SQLALCHEMY_DATABASE_URI", ""):
        _start_version_check_thread(app)

    return app


if __name__ == "__main__":  # pragma: no cover
    _debug = os.environ.get("FLASK_ENV") == "development"
    create_app().run(host="0.0.0.0", port=5000, debug=_debug)  # nosec B104
