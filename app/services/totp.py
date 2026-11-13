"""TOTP codes that can each be used once, in any worker (RFC 6238 §5.2).

A code matches one 30-second time step. The user's last accepted step is
claimed with a single conditional UPDATE, so a code (or an older one) is
refused once any code from that step or later was accepted — also by
another worker, or after a restart. The UPDATE bypasses ORM events on
purpose: recording a used step is not a credential change and must not
revoke the user's other logins (models._rotate_auth_version).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import cast

import pyotp
from models import User, db
from sqlalchemy import CursorResult, or_, update

# Accept the previous and next step too, for clock drift (as before).
_DRIFT_STEPS = (-1, 0, 1)


def _now() -> float:
    return time.time()


def matching_step(secret: str, code: str, now: float | None = None) -> int | None:
    """The time step `code` belongs to, if it is valid for `secret` now."""
    totp = pyotp.TOTP(secret)
    if not (code.isdigit() and len(code) == totp.digits):
        return None
    current = totp.timecode(datetime.fromtimestamp(_now() if now is None else now, UTC))
    for offset in _DRIFT_STEPS:
        step = current + offset
        if totp.generate_otp(step) == code:
            return step
    return None


ACCEPTED = "accepted"
INVALID = "invalid"
REUSED = "reused"


def use_code(user: User, code: str) -> str:
    """Accept `code` from the user's current authenticator at most once:
    ACCEPTED, INVALID (wrong code) or REUSED (valid, but its time step or a
    later one was already used). Commits the claimed step at once, so a
    racing request sees it."""
    if not user.totp_secret:
        return INVALID
    step = matching_step(str(user.totp_secret), code)
    if step is None:
        return INVALID
    result = db.session.execute(
        update(User)
        .where(
            User.id == user.id,
            User.totp_secret == user.totp_secret,
            or_(User.totp_last_step.is_(None), User.totp_last_step < step),
        )
        .values(totp_last_step=step)
        .execution_options(synchronize_session=False)
    )
    claimed = cast("CursorResult[object]", result).rowcount
    db.session.commit()
    return ACCEPTED if claimed else REUSED
