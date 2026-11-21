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
from passports.participation import active_participation


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
            *active_participation(),
            PassportParticipant.share_visits.is_(True),
        ).all()
    }
    return [
        p
        for p in passports
        if p.id in accepted
        or (
            include_history
            and (
                p.archived_at is not None
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
    # The viewer always sees their own stamps in an edition they joined, even
    # after they stop sharing visits; sharing only decides what others see.
    joined = {
        p.passport_id
        for p in PassportParticipant.query.filter(
            PassportParticipant.passport_id.in_([p.id for p in passports]),
            PassportParticipant.user_id == user_id,
            *active_participation(),
        ).all()
    }
    own = {
        **progress_many(
            [p for p in passports if p.id not in joined],
            user_id,
            include_evidence=False,
        ),
        **progress_many([p for p in passports if p.id in joined], user_id),
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
            *active_participation(),
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
    # Who shares visits in each edition, and which targets each of them has
    # visited: the basis of group statistics, keyed by user server-side only.
    roster: dict[int, dict[int, str]] = {p.id: {} for p in passports}
    visited_by: dict[int, dict[int, set[int]]] = {p.id: {} for p in passports}
    for participant in participants:
        if participant.user_id == user_id:
            if participant.passport_id in accepted:
                roster[participant.passport_id][user_id] = participant.display_name
            continue
        if participant.passport_id not in accepted and not participant.share_history:
            continue
        roster[participant.passport_id][participant.user_id] = participant.display_name
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
                visited_by[pid].setdefault(row["id"], set()).add(uid)
                others[pid].append(
                    {
                        "id": row["id"],
                        "user_id": uid,
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
    for pid, sharing in roster.items():
        if user_id in sharing:
            for row in own[pid]["rows"]:
                if row["visited"]:
                    visited_by[pid].setdefault(row["id"], set()).add(user_id)
    return {
        "own": own,
        "others": others,
        "other_counts": {identity: len(events) for identity, events in seen.items()},
        # Per participant, each visit counted once across overlapping editions.
        "user_counts": {
            identity: {
                uid: sum(1 for event in events if event[0] == uid)
                for uid in {event[0] for event in events}
            }
            for identity, events in seen.items()
        },
        "participating": accepted,
        "roster": roster,
        "visited_by": visited_by,
    }


def edition_stats(
    rows: list[dict[str, Any]],
    roster: dict[int, str],
    visited_by: dict[int, set[int]],
    viewer_id: int,
) -> dict[str, Any]:
    """Group coverage for one edition, over the participants sharing visits.

    The common denominator is every open target plus each closed target that
    at least one participant stamped, so closures never penalise anyone who
    got there first. Ranks are competition ranks (1, 1, 3). With nobody
    sharing, or nothing achievable, there is no percentage. User IDs stay
    server-side: participants get an opaque position as their filter key.
    """
    ordered = sorted(roster.items(), key=lambda item: (item[1].casefold(), item[0]))
    keys = {uid: index for index, (uid, _name) in enumerate(ordered, start=1)}
    counted = [
        row for row in rows if not row.get("is_closed") or visited_by.get(row["id"])
    ]
    denominator = len(counted)
    visits = {
        uid: sum(1 for row in counted if uid in visited_by.get(row["id"], set()))
        for uid in roster
    }
    board = sorted(
        ordered, key=lambda item: (-visits[item[0]], item[1].casefold(), item[0])
    )
    leaderboard: list[dict[str, Any]] = []
    for position, (uid, name) in enumerate(board, start=1):
        rank = (
            leaderboard[-1]["rank"]
            if leaderboard and leaderboard[-1]["visited"] == visits[uid]
            else position
        )
        leaderboard.append(
            {
                "key": keys[uid],
                "name": name,
                "you": uid == viewer_id,
                "visited": visits[uid],
                "rank": rank,
                "percent": round(100 * visits[uid] / denominator)
                if denominator
                else None,
            }
        )
    anyone = sum(1 for row in counted if visited_by.get(row["id"]))
    everyone = (
        sum(1 for row in counted if visited_by.get(row["id"], set()) >= set(roster))
        if roster
        else None
    )
    return {
        "participants": leaderboard,
        "denominator": denominator,
        "original": len(rows),
        "closed_excluded": len(rows) - denominator,
        "closed_included": sum(1 for row in counted if row.get("is_closed")),
        "anyone": anyone if roster else None,
        "everyone": everyone,
        "anyone_percent": round(100 * anyone / denominator)
        if roster and denominator
        else None,
        "everyone_percent": round(100 * everyone / denominator)
        if everyone is not None and denominator
        else None,
        "by_target": {
            row["id"]: {keys[uid] for uid in visited_by.get(row["id"], set())}
            for row in rows
        },
    }


def parse_count_scope(value: str | None) -> str | int:
    """The frequency scope asked for in a query string: "you" (default),
    "group", or a participant's position key; anything else means "you"."""
    from utils import list_index

    if value in {"you", "group"}:
        return value
    try:
        return list_index(value or "")
    except ValueError:
        return "you"


def count_scopes(
    passports: list[Passport], roster: dict[int, dict[int, str]], user_id: int
) -> tuple[list[dict[str, Any]], dict[int, int]]:
    """Frequency scopes of a shared view: "you", "group", then each other
    participant sharing visits, under an opaque position key (as the
    participant filter does) and their latest edition's group name."""
    names: dict[int, str] = {}
    for passport in editions.ordered(passports):
        names.update(roster.get(passport.id, {}))
    others = sorted(
        ((uid, name) for uid, name in names.items() if uid != user_id),
        key=lambda item: (item[1].casefold(), item[0]),
    )
    keyed = sorted(names.items(), key=lambda item: (item[1].casefold(), item[0]))
    keys = {uid: index for index, (uid, _name) in enumerate(keyed, start=1)}
    scopes: list[dict[str, Any]] = [{"key": "you"}, {"key": "group"}]
    scopes += [{"key": keys[uid], "name": name} for uid, name in others]
    return scopes, {keys[uid]: uid for uid in keys}


def combined(
    series: PassportSeries,
    passports: list[Passport],
    user_id: int,
    *,
    oldest: bool = False,
    current: Passport | None = None,
    colours: dict[int, str] | None = None,
    scope: str | int = "you",
) -> dict[str, Any]:
    """The shared map/list rows. Visit counts describe one explicit *scope*:
    the viewer's own visits by default, the whole group ("group": every
    participant sharing visits, one visit per participant per event), or one
    participant (their opaque key); never a silent mix."""
    evidence = shared_progress(series, passports, user_id)
    scopes, by_key = count_scopes(passports, evidence["roster"], user_id)
    if scope not in {"you", "group"} and scope not in by_key:
        scope = "you"
    chosen = by_key.get(scope) if isinstance(scope, int) else None
    if chosen == user_id:
        scope, chosen = "you", None
    sharing = {pid for pid, members in evidence["roster"].items() if user_id in members}
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
        per_user = evidence["user_counts"].get(row["id"], {})
        if scope == "group":
            # The viewer counts in the group only where they share visits.
            row["count"] = (row["count"] if sharing else 0) + sum(per_user.values())
        elif chosen is not None:
            row["count"] = per_user.get(chosen, 0)
        # Per-edition counts start as the viewer's own; others are added below.
        for edition in row["editions"]:
            if chosen is not None or (
                scope == "group" and edition["id"] not in sharing
            ):
                edition["count"] = 0
    for passport in editions.ordered(passports):
        for visit in evidence["others"][passport.id]:
            row = rows[visit["id"]]
            row["friends"].append(
                {
                    "user_id": visit["user_id"],
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
            if scope == "group" or visit["user_id"] == chosen:
                for edition in row["editions"]:
                    if edition["id"] == passport.id:
                        edition["count"] += visit["count"]
    for row in rows.values():
        friends: dict[int, dict[str, Any]] = {}
        for visit in row["friends"]:
            friend = friends.setdefault(
                visit["user_id"], {"name": visit["name"], "editions": []}
            )
            friend["name"] = visit["name"]  # Latest edition's group alias.
            friend["editions"].append(
                {"label": visit["edition"], "dates": visit["dates"]}
            )
        # Identity keys are for server-side deduplication, never shared flight IDs.
        row["friends"] = sorted(
            friends.values(), key=lambda friend: friend["name"].casefold()
        )
    view["participating"] = evidence["participating"]
    view["count_scopes"] = scopes
    view["count_scope"] = scope
    # The chosen participant's group name; "you" and "group" need none.
    view["count_scope_name"] = next(
        (option.get("name", "") for option in scopes if option["key"] == scope), ""
    )
    by_edition = {}
    for comparison in view["comparisons"]:
        passport_id = comparison["passport"].id
        stats = edition_stats(
            evidence["own"][passport_id]["rows"],
            evidence["roster"][passport_id],
            evidence["visited_by"][passport_id],
            user_id,
        )
        comparison["group_stats"] = stats
        by_edition[passport_id] = stats
    # A historical map can be selected while a different participating edition
    # is current. Its evidence is outside this view, so never index it here.
    view["stats"] = (
        by_edition.get(current.id)
        if current is not None and current.id in evidence["participating"]
        else None
    )
    return view


def validate_dates(passport: Passport, start: date | None, end: date | None) -> None:
    if passport.is_group and (start, end) != (passport.start_date, passport.end_date):
        raise BuilderError(
            _("Create a new edition to change the dates of a shared passport."), 409
        )
