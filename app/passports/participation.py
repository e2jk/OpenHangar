"""The one definition of an active, accepted passport participation.

Filters everywhere use it, so "who counts as a participant" cannot drift
between access checks, maps, statistics, reminders and public views.
"""

from typing import Any

from models import PassportParticipant


def active_participation() -> tuple[Any, ...]:
    """Conditions for a participation that is active, not blocked, accepted."""
    return (
        PassportParticipant.is_active.is_(True),
        PassportParticipant.blocked.is_(False),
        PassportParticipant.accepted_at.is_not(None),
    )
