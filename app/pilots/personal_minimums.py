"""
Personal minimums — starter templates and recency-nudge computation.

See docs/backlog.md "Pilots: personal minimums" for the implementation-ready
spec. Routes live in pilots/routes.py; this module holds the non-route logic
so the starter content and the recency math can be unit tested in isolation.
"""

import math
from datetime import date
from typing import Any

from flask_babel import lazy_gettext as _l  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    PersonalMinimumsRevision,
    PersonalMinimumsStatus,
    PersonalMinimumsTag,
)


def get_active_revision(uid: int) -> PersonalMinimumsRevision | None:
    revision: PersonalMinimumsRevision | None = (
        PersonalMinimumsRevision.query.filter_by(
            user_id=uid, status=PersonalMinimumsStatus.ACTIVE
        ).first()
    )
    return revision


# Each starter is a list of (section_title, [(item_label, tag), ...]).
# Values are intentionally left blank — the starter gives structure, not
# prescriptive numbers; the pilot fills in their own via the edit form.
# Labels are lazy-translated so pybabel's static extractor can see them
# despite living in a plain data structure rather than a template; callers
# must str() them at request time (see pilots.routes._create_starter_revision).
STARTER_LIGHT: list[tuple[object, list[tuple[object, str | None]]]] = [
    (
        _l("Winds"),
        [
            (_l("Max surface wind"), None),
            (_l("Max wind / gust differential"), None),
            (_l("Max crosswind component"), None),
        ],
    ),
    (
        _l("Weather"),
        [
            (_l("Minimum ceiling, day"), None),
            (_l("Minimum ceiling, night"), None),
            (_l("Minimum visibility, day"), None),
            (_l("Minimum visibility, night"), None),
        ],
    ),
    (
        _l("Fuel"),
        [
            (
                _l("Fuel reserve at landing (minutes)"),
                PersonalMinimumsTag.MIN_FUEL_RESERVE_MINUTES,
            ),
        ],
    ),
]

STARTER_FULL: list[tuple[object, list[tuple[object, str | None]]]] = [
    *STARTER_LIGHT,
    (
        _l("Guiding principles"),
        [
            (_l("Credo"), None),
            (_l("Flight-plan / route commitments"), None),
        ],
    ),
    (
        _l("Pre-flight checklists"),
        [
            (_l("PAVE (Pilot, Aircraft, enVironment, External pressures)"), None),
            (
                _l("I'M SAFE (Illness, Medication, Stress, Alcohol, Fatigue, Emotion)"),
                None,
            ),
        ],
    ),
    (
        _l("Ceilings by mission profile"),
        [
            (_l("Pattern work"), None),
            (_l("Local (< 50 nm)"), None),
            (_l("Short cross-country (< 100 nm)"), None),
            (_l("Long cross-country (> 100 nm)"), None),
        ],
    ),
    (
        _l("Performance"),
        [
            (_l("Cruise altitude without oxygen, max"), None),
            (_l("Minimum runway length at unfamiliar fields"), None),
        ],
    ),
    (
        _l("Night flying rules"),
        [
            (_l("Night flying commitments"), None),
        ],
    ),
    (
        _l("Decision-making rules"),
        [
            (_l("Three-strikes rule — pre-flight NO-GO"), None),
            (_l("Three-strikes rule — in-flight TERMINATE"), None),
        ],
    ),
    (
        _l("Recency commitments"),
        [
            (
                _l("Manoeuvres practice interval (months)"),
                PersonalMinimumsTag.MANOEUVRES_PRACTICE_INTERVAL_MONTHS,
            ),
            (
                _l("Familiar airports only after (days without flying)"),
                PersonalMinimumsTag.MAX_DAYS_SINCE_LAST_FLIGHT,
            ),
            (
                _l("Instructor flight after (days without flying)"),
                PersonalMinimumsTag.MAX_DAYS_SINCE_INSTRUCTOR_FLIGHT,
            ),
        ],
    ),
    (
        _l("Adjustments"),
        [
            (_l("If fatigued / unfamiliar aircraft / unfamiliar airport"), None),
        ],
    ),
]

STARTERS = {"light": STARTER_LIGHT, "full": STARTER_FULL}


def recency_breaches(revision: object, pilot_user_id: int) -> list[dict[str, Any]]:
    """Return a list of {item, days_since, threshold} for every item on
    `revision` tagged with a recency-checkable tag whose threshold has been
    exceeded. Only MAX_DAYS_SINCE_LAST_FLIGHT and
    MAX_DAYS_SINCE_INSTRUCTOR_FLIGHT are automatically checkable in v1."""
    from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

    breaches = []
    for section in revision.sections:  # type: ignore[attr-defined]
        for item in section.items:
            if item.semantic_tag not in PersonalMinimumsTag.HAS_RECENCY_CHECK:
                continue
            if item.numeric_value is None:
                continue
            threshold = float(item.numeric_value)
            if not math.isfinite(threshold):
                # Defense in depth: pilots/routes.py's _validate_tag_and_numeric
                # already rejects a non-finite numeric_value on write, but this
                # column has no DB-level schema enforcement — a corrupted or
                # otherwise-written value must degrade to "can't check this
                # item" (int(threshold) below would raise OverflowError for
                # inf), not crash the dashboard/notification check.
                continue
            query = PilotLogbookEntry.query.filter_by(pilot_user_id=pilot_user_id)
            if (
                item.semantic_tag
                == PersonalMinimumsTag.MAX_DAYS_SINCE_INSTRUCTOR_FLIGHT
            ):
                query = query.filter(PilotLogbookEntry.function_dual > 0)
            last_entry = query.order_by(PilotLogbookEntry.date.desc()).first()
            days_since = (
                (date.today() - last_entry.date).days
                if last_entry is not None
                else None
            )
            if days_since is None or days_since > threshold:
                breaches.append(
                    {
                        "item": item,
                        "days_since": days_since,
                        "threshold": int(threshold),
                    }
                )
    return breaches
