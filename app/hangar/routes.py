from flask import Blueprint, render_template
from flask.typing import ResponseReturnValue  # pyright: ignore[reportMissingImports]

hangar_bp = Blueprint("hangar", __name__)


@hangar_bp.route("/hangar/secret")
def secret() -> ResponseReturnValue:
    return render_template("hangar/secret.html")
