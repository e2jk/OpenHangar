"""Useful labels for a saved link whose place name can be filled in later."""

from urllib.parse import urlsplit

from flask_babel import gettext as _
from models import Place


def place_label(place: Place) -> str:
    if place.title:
        return str(place.title)
    if place.external_url:
        hostname = urlsplit(place.external_url).hostname
        if hostname:
            return str(hostname)
    return str(_("Unnamed place"))
