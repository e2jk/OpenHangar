"""Authenticated group discussions; neither stamps nor private flight notes."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from flask_babel import gettext as _
from models import (
    Passport,
    PassportAirfield,
    PassportAirfieldComment,
    PassportAirfieldIdentity,
    PassportSeries,
    PassportSeriesMember,
    db,
)
from sqlalchemy.exc import IntegrityError

from passports import groups
from passports.management import BuilderError, require_actor

CATEGORIES = ("airfield", "nearby", "question")
MAX_BODY = 8000


@dataclass
class Context:
    series: PassportSeries
    airfield: PassportAirfieldIdentity
    member: PassportSeriesMember


def context(
    tenant_id: int,
    user_id: int,
    series_id: int,
    airfield_id: int,
    *,
    writing: bool = False,
) -> Context:
    if writing:
        require_actor(tenant_id, user_id)
    query = PassportSeries.query.filter_by(
        id=series_id, tenant_id=tenant_id, is_group=True
    )
    series = query.populate_existing().first()
    if series is None:
        raise BuilderError(_("Group discussion not found."), 404)
    member = groups.require_series(series, user_id)
    if writing:
        # Do not let an unauthorised caller lock another group's mutations.
        # Recheck membership/ownership after acquiring the shared group mutex.
        groups.lock_series(series.id)
        series = query.populate_existing().first()
        if series is None:
            raise BuilderError(_("Group discussion not found."), 404)
        member = groups.require_series(series, user_id)
    airfield = PassportAirfieldIdentity.query.filter_by(
        id=airfield_id, tenant_id=tenant_id
    ).first()
    referenced = (
        db.session.query(PassportAirfield.id)
        .join(Passport)
        .filter(
            Passport.series_id == series.id,
            Passport.is_group.is_(True),
            PassportAirfield.airfield_id == airfield_id,
        )
        .first()
    )
    # A discussion outlives removal of the target from an edition. It never
    # makes a private linked passport's field a group topic.
    retained = PassportAirfieldComment.query.filter_by(
        tenant_id=tenant_id, series_id=series.id, airfield_id=airfield_id
    ).first()
    if airfield is None or (referenced is None and retained is None):
        raise BuilderError(_("Group discussion not found."), 404)
    return Context(series, airfield, member)


def _values(values: Any) -> dict[str, Any]:
    body = values.get("body", "").strip()
    if not body or len(body) > MAX_BODY:
        raise BuilderError(_("Write a comment of 1 to 8,000 characters."))
    category = values.get("category", "airfield")
    if category not in CATEGORIES:
        raise BuilderError(_("Choose a comment category."))
    experience = None
    if values.get("experience_date", "").strip():
        try:
            experience = date.fromisoformat(values["experience_date"])
        except ValueError as exc:
            raise BuilderError(_("Enter a valid experience date.")) from exc
        if experience > datetime.now(UTC).date():
            raise BuilderError(_("Experience date cannot be in the future."))
    return {"body": body, "category": category, "experience_date": experience}


def _flush() -> None:
    try:
        db.session.flush()
    except IntegrityError as exc:
        raise BuilderError(
            _("The group changed. Reload the discussion and try again."), 409
        ) from exc


def add(
    tenant_id: int, user_id: int, series_id: int, airfield_id: int, values: Any
) -> PassportAirfieldComment:
    scope = context(tenant_id, user_id, series_id, airfield_id, writing=True)
    comment = PassportAirfieldComment(
        tenant_id=tenant_id,
        series_id=series_id,
        airfield_id=airfield_id,
        author_user_id=user_id,
        author_display_name=scope.member.display_name,
        **_values(values),
    )
    db.session.add(comment)
    _flush()
    return comment


def change(
    tenant_id: int,
    user_id: int,
    series_id: int,
    airfield_id: int,
    comment_id: int,
    values: Any,
    *,
    delete: bool = False,
) -> None:
    scope = context(tenant_id, user_id, series_id, airfield_id, writing=True)
    comment = (
        PassportAirfieldComment.query.filter_by(
            id=comment_id,
            tenant_id=tenant_id,
            series_id=series_id,
            airfield_id=airfield_id,
        )
        .with_for_update()
        .populate_existing()
        .first()
    )
    if comment is None:
        raise BuilderError(_("Comment not found."), 404)
    author = comment.author_user_id == user_id
    if not author and not (delete and scope.series.owner_user_id == user_id):
        raise BuilderError(_("You cannot change this comment."), 403)
    if delete:
        db.session.delete(comment)
    else:
        for key, value in _values(values).items():
            setattr(comment, key, value)
        comment.edited_at = datetime.now(UTC)
    _flush()


def feed(
    scope: Context, *, category: str = "", after: str = "", before: str = ""
) -> Any:
    """A date/category-filtered query for a freshly authorised group context."""
    if category and category not in CATEGORIES:
        raise BuilderError(_("Choose a comment category."))
    try:
        first = date.fromisoformat(after) if after else None
        last = date.fromisoformat(before) if before else None
    except ValueError as exc:
        raise BuilderError(_("Enter a valid posting date.")) from exc
    if first and last and first > last:
        raise BuilderError(_("The posting date range is reversed."))
    query = PassportAirfieldComment.query.filter_by(
        tenant_id=scope.series.tenant_id,
        series_id=scope.series.id,
        airfield_id=scope.airfield.id,
    )
    if category:
        query = query.filter(PassportAirfieldComment.category == category)
    if first:
        query = query.filter(
            PassportAirfieldComment.created_at
            >= datetime.combine(first, datetime.min.time(), UTC)
        )
    if last:
        query = query.filter(
            PassportAirfieldComment.created_at
            <= datetime.combine(last, datetime.max.time(), UTC)
        )
    return query.order_by(
        PassportAirfieldComment.created_at.desc(), PassportAirfieldComment.id.desc()
    )
