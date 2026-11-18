"""Explicit group consent and history scope, independent of edition participation.

Only allowlisted shared evidence leaves this module. Callers commit mutations.
"""

from datetime import date
from typing import Any, NoReturn

from flask_babel import gettext as _
from models import (
    Passport,
    PassportParticipant,
    PassportSeries,
    PassportSeriesMember,
    TenantUser,
    User,
    db,
    fits_column,
)
from sqlalchemy import text
from sqlalchemy.exc import NoResultFound, OperationalError

from passports import editions
from passports.eligibility import progress_many
from passports.management import BuilderError, owned_passport, require_actor


def lock_series(series_id: int) -> None:
    """Serialize membership changes; bound waits so a busy import cannot stall joins."""
    if db.engine.dialect.name == "postgresql":
        try:
            db.session.execute(text("SET LOCAL lock_timeout = '1s'"))
            db.session.execute(
                text("SELECT pg_advisory_xact_lock(:namespace, :series_id)"),
                {"namespace": 0x4F485047, "series_id": series_id},
            )
        except OperationalError as exc:
            lock_failure(exc)


def lock_failure(exc: OperationalError) -> NoReturn:
    if getattr(exc.orig, "sqlstate", None) in {"55P03", "40P01"}:
        raise BuilderError(
            _("This group is being updated. Please try again."), 409
        ) from exc
    raise exc


def lock_passport(passport: Passport) -> Passport:
    series_id = passport.series_id
    try:
        locked: Passport = (
            Passport.query.filter_by(id=passport.id, tenant_id=passport.tenant_id)
            .with_for_update()
            .populate_existing()
            .one()
        )
    except OperationalError as exc:
        lock_failure(exc)
    except NoResultFound as exc:
        raise BuilderError(_("Group not found."), 404) from exc
    if locked.series_id != series_id:
        raise BuilderError(_("This passport changed. Open it again."), 409)
    return locked


def membership(series: PassportSeries, user_id: int) -> PassportSeriesMember | None:
    return (
        db.session.query(PassportSeriesMember)
        .join(User, User.id == PassportSeriesMember.user_id)
        .join(
            TenantUser,
            (TenantUser.user_id == User.id)
            & (TenantUser.tenant_id == series.tenant_id),
        )
        .filter(
            PassportSeriesMember.series_id == series.id,
            PassportSeriesMember.tenant_id == series.tenant_id,
            PassportSeriesMember.user_id == user_id,
            PassportSeriesMember.is_active.is_(True),
            PassportSeriesMember.blocked.is_(False),
            User.is_active.is_(True),
        )
        .first()
    )


def require_series(series: PassportSeries, user_id: int) -> PassportSeriesMember:
    member = membership(series, user_id) if series.is_group else None
    if member is None:
        raise BuilderError(_("Group not found."), 404)
    return member


def consent(
    passport: Passport, user_id: int, values: dict[str, str]
) -> PassportSeriesMember:
    """Existing participants accept sharing; invitations will call this after enrolment."""
    require_actor(passport.tenant_id, user_id)
    if passport.series_id is not None:
        lock_series(passport.series_id)
        passport = lock_passport(passport)
    series = (
        db.session.get(PassportSeries, passport.series_id)
        if passport.series_id is not None
        else None
    )
    participant = (
        PassportParticipant.query.filter_by(
            passport_id=passport.id, user_id=user_id, is_active=True
        )
        .filter(PassportParticipant.accepted_at.is_not(None))
        .populate_existing()
        .first()
    )
    if (
        not passport.is_group
        or series is None
        or not series.is_group
        or participant is None
        or participant.blocked
    ):
        raise BuilderError(_("Group not found."), 404)
    member = db.session.get(
        PassportSeriesMember, (series.id, user_id), populate_existing=True
    )
    if member is not None and member.blocked:
        raise BuilderError(_("The group owner must reinstate your membership."), 403)
    name = values.get("display_name", "").strip()
    if not name or not fits_column(PassportSeriesMember.display_name, name):
        raise BuilderError(_("Enter a group display name of at most 128 characters."))
    if values.get("share_visits") != "yes":
        raise BuilderError(
            _(
                "Confirm sharing your eligible visit dates with this edition's participants."
            )
        )
    if member is None:
        member = PassportSeriesMember(
            series_id=series.id,
            user_id=user_id,
            tenant_id=series.tenant_id,
            display_name=name,
        )
        db.session.add(member)
    member.is_active = True
    member.display_name = name
    participant.display_name = name
    participant.share_visits = True
    participant.share_history = values.get("share_history") == "yes"
    return member


def enable(passport: Passport, user_id: int, values: dict[str, str]) -> PassportSeries:
    original_series_id = passport.series_id
    if original_series_id is not None:
        lock_series(original_series_id)
    try:
        owned_passport(passport.tenant_id, user_id, passport.id)
    except OperationalError as exc:
        lock_failure(exc)
    if passport.series_id != original_series_id:
        raise BuilderError(_("This passport changed. Open it again."), 409)
    if passport.series_id is None:
        series = editions.link(passport, user_id, title=values.get("series_title", ""))
    else:
        series = editions.owned_series(passport.tenant_id, user_id, passport.series_id)
    series.is_group = True
    passport.is_group = True
    consent(passport, user_id, values)
    return series


def visible_editions(
    series: PassportSeries, user_id: int, *, include_history: bool = False
) -> tuple[list[Passport], set[int]]:
    require_series(series, user_id)
    passports = editions.ordered(
        Passport.query.filter_by(
            tenant_id=series.tenant_id, series_id=series.id, is_group=True
        ).all()
    )
    accepted = {
        p.passport_id
        for p in PassportParticipant.query.filter(
            PassportParticipant.passport_id.in_([p.id for p in passports]),
            PassportParticipant.user_id == user_id,
            PassportParticipant.is_active.is_(True),
            PassportParticipant.blocked.is_(False),
            PassportParticipant.accepted_at.is_not(None),
            PassportParticipant.share_visits.is_(True),
        ).all()
    }
    earliest = next(
        (i for i, p in enumerate(passports) if p.id in accepted), len(passports)
    )
    return [
        p
        for i, p in enumerate(passports)
        if p.id in accepted
        or (
            include_history
            and i < earliest
            and (
                p.id in accepted
                or p.archived_at is not None
                or (p.end_date is not None and p.end_date < date.today())
            )
        )
    ], accepted


def shared_progress(
    series: PassportSeries, passports: list[Passport], user_id: int
) -> dict[str, Any]:
    """Batch each consenting pilot once across editions; strip all private fields."""
    require_series(series, user_id)
    permitted, accepted = visible_editions(series, user_id, include_history=True)
    allowed_ids = {p.id for p in permitted}
    if any(p.id not in allowed_ids or p.series_id != series.id for p in passports):
        raise BuilderError(_("Group not found."), 404)
    own = {
        **progress_many(
            [p for p in passports if p.id not in accepted],
            user_id,
            include_evidence=False,
        ),
        **progress_many([p for p in passports if p.id in accepted], user_id),
    }
    participants = (
        db.session.query(PassportParticipant)
        .join(
            PassportSeriesMember,
            (PassportSeriesMember.user_id == PassportParticipant.user_id)
            & (PassportSeriesMember.series_id == series.id),
        )
        .join(User, User.id == PassportParticipant.user_id)
        .join(
            TenantUser,
            (TenantUser.user_id == User.id)
            & (TenantUser.tenant_id == series.tenant_id),
        )
        .filter(
            PassportParticipant.passport_id.in_([p.id for p in passports]),
            PassportParticipant.is_active.is_(True),
            PassportParticipant.blocked.is_(False),
            PassportParticipant.accepted_at.is_not(None),
            PassportParticipant.share_visits.is_(True),
            PassportSeriesMember.is_active.is_(True),
            PassportSeriesMember.blocked.is_(False),
            User.is_active.is_(True),
        )
        .all()
    )
    by_user: dict[int, list[Passport]] = {}
    names: dict[tuple[int, int], str] = {}
    p_by_id = {p.id: p for p in passports}
    for participant in participants:
        if participant.user_id == user_id or (
            participant.passport_id not in accepted and not participant.share_history
        ):
            continue
        by_user.setdefault(participant.user_id, []).append(
            p_by_id[participant.passport_id]
        )
        names[participant.user_id, participant.passport_id] = participant.display_name
    others: dict[int, list[dict[str, Any]]] = {p.id: [] for p in passports}
    # Event identifiers remain server-side, solely for cross-edition deduplication.
    seen: dict[int, set[tuple[int, str, int]]] = {}
    for uid, collection in by_user.items():
        for pid, progress in progress_many(collection, uid).items():
            for row in progress["rows"]:
                events = [e for e in row["events"] if not e["excluded"]]
                if not events:
                    continue
                others[pid].append(
                    {
                        "id": row["id"],
                        "name": names[uid, pid],
                        "dates": sorted({e["date"].isoformat() for e in events}),
                        "count": row["count"],
                    }
                )
                for event in events:
                    seen.setdefault(row["id"], set()).add(
                        (
                            uid,
                            "flight" if event["flight_id"] is not None else "visit",
                            event["flight_id"] or event["id"],
                        )
                    )
    return {
        "own": own,
        "others": others,
        "other_counts": {identity: len(events) for identity, events in seen.items()},
        "participating": accepted,
    }


def combined(
    series: PassportSeries,
    passports: list[Passport],
    user_id: int,
    *,
    oldest: bool = False,
    current: Passport | None = None,
    colours: dict[int, str] | None = None,
) -> dict[str, Any]:
    evidence = shared_progress(series, passports, user_id)
    view = editions.combined(
        passports,
        user_id,
        oldest=oldest,
        current=current,
        colours=colours,
        progress=evidence["own"],
    )
    rows = {r["id"]: r for r in view["rows"]}
    for row in rows.values():
        row["own_count"] = row["count"]
        row["state"] = "own" if row["visited"] else "unvisited"
        row["friends"] = []
        row["count"] += evidence["other_counts"].get(row["id"], 0)
    for passport in editions.ordered(passports):
        for visit in evidence["others"][passport.id]:
            row = rows[visit["id"]]
            row["friends"].append(
                {
                    "name": visit["name"],
                    "edition": passport.edition_label or passport.title,
                    "dates": visit["dates"],
                }
            )
            if row["state"] != "own":
                row["state"] = "others"
                if row["colour"] is None or not oldest:
                    row["colour"] = view["colours"][passport.id]
                    row["edition_label"] = passport.edition_label or passport.title
            for edition in row["editions"]:
                if edition["id"] == passport.id:
                    edition["count"] += visit["count"]
    view["participating"] = evidence["participating"]
    return view


def validate_dates(passport: Passport, start: date | None, end: date | None) -> None:
    if passport.is_group and (start, end) != (passport.start_date, passport.end_date):
        raise BuilderError(
            _("Create a new edition to change the dates of a shared passport."), 409
        )
