from flask import Blueprint, abort, render_template

squawk_bp = Blueprint("squawk", __name__)

_KNOWN = {7700, 7600, 7500, 7000, 1200}


@squawk_bp.route("/squawk/<int:code>")
def squawk(code: int):
    if code not in _KNOWN:
        abort(404)
    return render_template(f"squawk/{code}.html")
