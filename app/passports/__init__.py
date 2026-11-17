"""Airfield passports; builder, maps and invitations follow the data foundation."""

from flask import Blueprint

passports_bp = Blueprint("passports", __name__, url_prefix="/passports")
