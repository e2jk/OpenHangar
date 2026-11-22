"""Where a place photo was taken, to offer it as the place's pin (backlog
"Photos: more suggestions"). Read in memory and discarded; the photo saved
with the place is cleaned of all its metadata (services.images)."""

from __future__ import annotations

from flask import abort, jsonify, request
from flask.typing import ResponseReturnValue
from services.images import MAX_PHOTO_BYTES
from services.photo_hints import read_hints
from utils import login_required

from places.access import actor
from places.routes import context, places_bp


@places_bp.route("/photo-position", methods=["POST"])
@login_required
def photo_position() -> ResponseReturnValue:
    tid, uid = context()
    actor(tid, uid)
    upload = request.files.get("photo")
    if upload is None or not upload.filename:
        abort(400)
    hints = read_hints(upload.stream.read(MAX_PHOTO_BYTES + 1), upload.filename, None)
    return jsonify(position=hints["position"])
