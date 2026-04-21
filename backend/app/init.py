import os

from flask import Flask, render_template, request, session # pyright: ignore[reportMissingImports]
from flask_migrate import Migrate # type: ignore


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

    from backup.routes import backup_bp
    app.register_blueprint(backup_bp)

    from share.routes import share_bp
    app.register_blueprint(share_bp)

    if flask_env == "demo":
        from demo.routes import demo_bp
        app.register_blueprint(demo_bp)

    @app.context_processor
    def inject_globals():
        from models import DemoSlot, User
        is_demo = flask_env == "demo"
        demo_next_wipe_utc = os.environ.get("DEMO_NEXT_WIPE_UTC") if is_demo else None
        demo_site_url = os.environ.get("DEMO_SITE_URL")
        demo_display_id = None
        if is_demo:
            slot_id = session.get("demo_slot_id")
            if slot_id:
                slot = db.session.get(DemoSlot, slot_id)
                if slot:
                    demo_display_id = slot.display_id
        return {
            "logged_in": bool(session.get("user_id")),
            "has_users": User.query.count() > 0,
            "flask_env": flask_env,
            "is_demo": is_demo,
            "demo_next_wipe_utc": demo_next_wipe_utc,
            "demo_display_id": demo_display_id,
            "demo_site_url": demo_site_url,
        }

    @app.route("/")
    def index():
        from models import Aircraft, TenantUser, User

        # Demo mode: unauthenticated visitors always see the landing page
        if flask_env == "demo" and not session.get("user_id"):
            return render_template("landing.html")

        if User.query.count() == 0:
            return render_template("landing.html")
        if session.get("user_id"):
            from datetime import date as _date
            from models import FlightEntry, MaintenanceTrigger
            from utils import compute_aircraft_statuses
            tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
            aircraft = (
                Aircraft.query
                .filter_by(tenant_id=tu.tenant_id)
                .order_by(Aircraft.registration)
                .all()
            ) if tu else []
            aircraft_ids = [ac.id for ac in aircraft]
            hobbs_by_aircraft = {ac.id: ac.total_hobbs for ac in aircraft}

            recent_flights = (
                FlightEntry.query
                .filter(FlightEntry.aircraft_id.in_(aircraft_ids))
                .order_by(FlightEntry.date.desc(), FlightEntry.id.desc())
                .limit(5)
                .all()
            ) if aircraft_ids else []

            today = _date.today()
            month_start = today.replace(day=1)
            month_flights = (
                FlightEntry.query
                .filter(
                    FlightEntry.aircraft_id.in_(aircraft_ids),
                    FlightEntry.date >= month_start,
                )
                .all()
            ) if aircraft_ids else []
            hours_this_month = sum(
                float(f.hobbs_end) - float(f.hobbs_start) for f in month_flights
            )
            flights_this_month = len(month_flights)

            triggers = (
                MaintenanceTrigger.query
                .filter(MaintenanceTrigger.aircraft_id.in_(aircraft_ids))
                .all()
            ) if aircraft_ids else []

            aircraft_status = compute_aircraft_statuses(aircraft, triggers, hobbs_by_aircraft)

            urgent_maintenance = []
            maintenance_alerts = 0
            ac_by_id = {ac.id: ac for ac in aircraft}
            for t in triggers:
                s = t.status(hobbs_by_aircraft.get(t.aircraft_id))
                if s in ("overdue", "due_soon"):
                    maintenance_alerts += 1
                    urgent_maintenance.append((t, s, ac_by_id[t.aircraft_id]))
            urgent_maintenance.sort(key=lambda x: (0 if x[1] == "overdue" else 1))
            urgent_maintenance = urgent_maintenance[:5]

            return render_template("dashboard.html", aircraft=aircraft,
                                   recent_flights=recent_flights,
                                   hours_this_month=hours_this_month,
                                   flights_this_month=flights_this_month,
                                   maintenance_alerts=maintenance_alerts,
                                   urgent_maintenance=urgent_maintenance,
                                   aircraft_status=aircraft_status)
        return render_template("welcome.html")

    @app.route("/not-yet-implemented")
    def not_yet_implemented():
        feature = request.args.get("feature", "This feature")
        return render_template("not_yet_implemented.html", feature=feature), 501

    @app.route("/health")
    def health():
        return {"status": "ok"}, 200

    @app.cli.command("backup-now")
    def backup_now_command():  # pragma: no cover
        from backup.routes import run_backup
        try:
            record = run_backup()
            print(f"Backup OK: {record.filename} ({record.size_bytes} bytes, sha256={record.sha256})")
        except RuntimeError as exc:
            print(f"Backup FAILED: {exc}")

    # Flask CLI command used by demo/refresh.sh to wipe and reseed demo slots
    @app.cli.command("seed-demo")
    def seed_demo_command():  # pragma: no cover
        from demo_seed import seed as demo_seed
        demo_seed()
        print("Demo slots reseeded.")

    return app


if __name__ == "__main__":  # pragma: no cover
    create_app().run(host="0.0.0.0", port=5000, debug=True)
