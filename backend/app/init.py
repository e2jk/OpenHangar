import os

from flask import Flask, render_template, session
from flask_migrate import Migrate


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
        from models import User
        if User.query.count() == 0:
            return render_template("index.html")
        if session.get("user_id"):
            return render_template("dashboard.html")
        return render_template("welcome.html")

    @app.route("/health")
    def health():
        return {"status": "ok"}, 200

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5000, debug=True)
