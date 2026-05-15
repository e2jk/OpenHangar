import os
import sqlite3
from urllib.parse import urlparse

from flask import Flask, render_template, request, send_from_directory, session  # pyright: ignore[reportMissingImports]
from flask_babel import Babel, get_locale as _babel_get_locale  # pyright: ignore[reportMissingImports]
from flask_migrate import Migrate  # type: ignore
from sqlalchemy import event  # pyright: ignore[reportMissingImports]
from sqlalchemy.engine import Engine  # pyright: ignore[reportMissingImports]

SUPPORTED_LOCALES = ["en", "fr", "nl"]

LOCALE_META = {
    "en": {"flag": "🇬🇧", "abbr": "EN", "native": "English", "english": "English"},
    "fr": {"flag": "🇫🇷", "abbr": "FR", "native": "Français", "english": "French"},
    "nl": {"flag": "🇳🇱", "abbr": "NL", "native": "Nederlands", "english": "Dutch"},
}


@event.listens_for(Engine, "connect")
def _set_sqlite_fk_pragma(dbapi_connection, _record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def create_app():
    app = Flask(__name__)

    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", "sqlite:///:memory:"
    )
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["UPLOAD_FOLDER"] = os.environ.get("UPLOAD_FOLDER", "/data/uploads")
    app.config["BACKUP_FOLDER"] = os.environ.get("BACKUP_FOLDER", "/data/backups")

    flask_env = os.environ.get("FLASK_ENV", "production")

    if flask_env == "development":
        app.config["TEMPLATES_AUTO_RELOAD"] = True

    from models import db

    db.init_app(app)
    Migrate(app, db)

    def _get_locale():
        if session.get("user_id"):
            # Demo sessions: visitor's session language takes precedence over the
            # demo user's stored default so Accept-Language / manual switcher work.
            if (
                session.get("demo_slot_id")
                and session.get("language") in SUPPORTED_LOCALES
            ):
                return session["language"]
            from models import User

            user = db.session.get(User, session["user_id"])
            if user and user.language in SUPPORTED_LOCALES:
                return user.language
        if session.get("language") in SUPPORTED_LOCALES:
            return session["language"]
        return request.accept_languages.best_match(SUPPORTED_LOCALES, default="en")

    Babel(app, locale_selector=_get_locale)

    from flask_babel import format_date, format_datetime, format_decimal

    app.jinja_env.globals.update(
        format_date=format_date,
        format_datetime=format_datetime,
        format_decimal=format_decimal,
    )

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

    if flask_env == "demo":
        from demo.routes import demo_bp

        app.register_blueprint(demo_bp)

    @app.context_processor
    def inject_globals():
        from models import DemoSlot, Role, User
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
        }

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.route("/")
    def index():
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

            cal_day_events: dict = defaultdict(
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
    def not_yet_implemented():
        feature = request.args.get("feature", "This feature")
        return render_template("not_yet_implemented.html", feature=feature), 501

    @app.route("/set-language/<lang>")
    def set_language(lang):
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
            session["language"] = lang
        _ref = (request.referrer or "").replace("\\", "")
        _parsed_ref = urlparse(_ref)
        return redirect(
            _ref if (not _parsed_ref.scheme and not _parsed_ref.netloc) else "/"
        )

    @app.route("/favicon.ico")
    def favicon():
        return send_from_directory(
            app.static_folder, "favicon.svg", mimetype="image/svg+xml"
        )

    @app.route("/health")
    def health():
        return {"status": "ok"}, 200

    @app.cli.command("backup-now")
    def backup_now_command():  # pragma: no cover
        from config.routes import run_backup

        try:
            record = run_backup()
            print(
                f"Backup OK: {record.filename} ({record.size_bytes} bytes, sha256={record.sha256})"
            )
        except RuntimeError as exc:
            print(f"Backup FAILED: {exc}")

    # Flask CLI command used by demo/refresh.sh to drop and recreate the schema.
    # Only works in demo mode — production uses Alembic migrations.
    @app.cli.command("reset-db")
    def reset_db_command():  # pragma: no cover
        if flask_env != "demo":
            print("reset-db is only available in demo mode. Aborting.")
            return
        db.drop_all()
        db.create_all()
        print("Database schema reset.")

    # Flask CLI command used by demo/refresh.sh to wipe and reseed demo slots
    @app.cli.command("seed-demo")
    def seed_demo_command():  # pragma: no cover
        from demo_seed import seed as demo_seed

        demo_seed()
        print("Demo slots reseeded.")

    return app


if __name__ == "__main__":  # pragma: no cover
    _debug = os.environ.get("FLASK_ENV") == "development"
    create_app().run(host="0.0.0.0", port=5000, debug=_debug)
