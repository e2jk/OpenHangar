import os
from flask import Flask, render_template

def create_app():
    app = Flask(__name__)

    if os.environ.get("FLASK_ENV") == "development":
        app.config["TEMPLATES_AUTO_RELOAD"] = True

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/health")
    def health():
        return {"status": "ok"}, 200

    return app

if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5000, debug=True)
