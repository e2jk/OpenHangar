"""Passport selection policy over the shared waypoint serializer."""

from collections.abc import Iterable
from typing import Any

from flask_babel import gettext as _
from services import waypoints
from services.waypoints import FORMATS, Selection, Waypoint

__all__ = ["FORMATS", "Selection", "Waypoint"]


def select(
    rows: Iterable[dict[str, Any]],
    *,
    missing_rows: Iterable[dict[str, Any]] | None = None,
) -> Selection:
    def eligible(items: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
        return (row for row in items if not row["visited"] and not row.get("is_closed"))

    selection = waypoints.select(eligible(rows))
    if missing_rows is not None:
        return Selection(
            selection.points, waypoints.select(eligible(missing_rows)).omitted
        )
    return selection


def document(selection: Selection, fmt: str) -> bytes:
    return waypoints.document(selection, fmt, label=_("Unvisited airfields"))
