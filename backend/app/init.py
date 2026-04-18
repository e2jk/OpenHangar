import os

from flask import Flask, render_template, session # pyright: ignore[reportMissingImports]
from flask_migrate import Migrate # type: ignore


def create_app():
    app = Flask(__name__)

    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", "sqlite:///:memory:"
    )
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    if os.environ.get("FLASK_ENV") == "development":
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

    @app.context_processor
    def inject_globals():
        from models import User
        flask_env = os.environ.get("FLASK_ENV", "production")
        return {
            "logged_in": bool(session.get("user_id")),
            "has_users": User.query.count() > 0,
            "flask_env": flask_env,
        }

    @app.route("/")
    def index():
        from models import Aircraft, TenantUser, User
        if User.query.count() == 0:
            return render_template("landing.html")
        if session.get("user_id"):
            from models import FlightEntry, MaintenanceTrigger
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
            urgent_maintenance = []
            maintenance_alerts = 0
            if aircraft_ids:
                triggers = MaintenanceTrigger.query.filter(
                    MaintenanceTrigger.aircraft_id.in_(aircraft_ids)
                ).all()
                ac_by_id = {ac.id: ac for ac in aircraft}
                for t in triggers:
                    s = t.status(hobbs_by_aircraft.get(t.aircraft_id))
                    if s in ("overdue", "due_soon"):
                        maintenance_alerts += 1
                        urgent_maintenance.append((t, s, ac_by_id[t.aircraft_id]))
                # Sort overdue first, then due_soon; limit to 5
                urgent_maintenance.sort(key=lambda x: (0 if x[1] == "overdue" else 1))
                urgent_maintenance = urgent_maintenance[:5]
            return render_template("dashboard.html", aircraft=aircraft,
                                   recent_flights=recent_flights,
                                   maintenance_alerts=maintenance_alerts,
                                   urgent_maintenance=urgent_maintenance)
        return render_template("welcome.html")

    @app.route("/health")
    def health():
        return {"status": "ok"}, 200

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5000, debug=True)
