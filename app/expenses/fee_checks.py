"""Suggest likely missing movement fees from previous comparable flights."""

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import groupby

from models import (
    DataCheckDismissal,
    Expense,
    ExpenseType,
    Flight,
    LogbookEntryType,
    db,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

LANDING = ExpenseType.LANDING_FEE
TAKEOFF = ExpenseType.TAKEOFF_FEE
KINDS = {LANDING: "arrival_icao", TAKEOFF: "departure_icao"}
FEE_TYPES = (*KINDS, ExpenseType.AIRFIELD_FEES)
# Recognise explicit labels on old "Other" expenses, including labels followed
# by a reference separated by punctuation. Ambiguous airport invoices need review.
LEGACY_LABELS = {
    LANDING: (
        "landing fee",
        "landing fees",
        "taxe d’atterrissage",
        "redevance d’atterrissage",
        "landingsgeld",
        "landingskosten",
    ),
    TAKEOFF: (
        "takeoff fee",
        "take-off fee",
        "takeoff fees",
        "take-off fees",
        "taxe de décollage",
        "redevance de décollage",
        "startgeld",
        "startkosten",
    ),
}
HISTORY = timedelta(days=365)
INVOICE_GRACE = timedelta(days=7)
DEFER_FOR = timedelta(days=30)
INVOICE_WINDOW = timedelta(days=30)


@dataclass(frozen=True)
class MissingFee:
    flight: Flight
    kind: str
    airfield: str
    charged: int
    movements: int


def airfield_for(flight: Flight, kind: str) -> str:
    code = getattr(flight, KINDS[kind]) or ""
    return str(code).strip().upper()


def fee_kinds(expense: Expense) -> set[str]:
    if expense.expense_type == ExpenseType.AIRFIELD_FEES:
        return set(KINDS)
    if expense.expense_type in KINDS:
        return {expense.expense_type}
    if expense.expense_type != ExpenseType.OTHER:
        return set()
    label = (expense.description or "").strip().casefold().replace("'", "’")
    return {
        kind
        for kind, labels in LEGACY_LABELS.items()
        if any(
            label == name or label.startswith((name + ":", name + " -", name + " —"))
            for name in labels
        )
    }


def feedback_key(kind: str, airfield: str, decision: str) -> str:
    return f"fee:{decision}:{kind}:{airfield}"[:96]


def missing_fees(
    aircraft_ids: list[int], start: date, end: date, today: date
) -> list[MissingFee]:
    first_day = start - min(HISTORY, start - date.min)
    flights = (
        Flight.query.filter(
            Flight.aircraft_id.in_(aircraft_ids),
            Flight.entry_type == LogbookEntryType.FLIGHT,
            Flight.date.between(first_day, end),
        )
        .order_by(Flight.date, Flight.id)
        .all()
    )
    flight_scope = Flight.query.filter(
        Flight.aircraft_id.in_(aircraft_ids),
        Flight.entry_type == LogbookEntryType.FLIGHT,
        Flight.date.between(first_day, end),
    ).with_entities(Flight.id)
    status_by_flight: dict[int, dict[str, bool]] = defaultdict(dict)
    for expense in Expense.query.join(
        Flight, Flight.id == Expense.flight_entry_id
    ).filter(
        Expense.aircraft_id == Flight.aircraft_id,
        Expense.flight_entry_id.in_(flight_scope),
        Expense.expense_type.in_((*FEE_TYPES, ExpenseType.OTHER)),
    ):
        statuses = status_by_flight[expense.flight_entry_id]
        for kind in fee_kinds(expense):
            statuses[kind] = statuses.get(kind, False) or expense.amount > 0

    feedback = {
        (row.flight_id, row.key): row.created_at.date()
        for row in DataCheckDismissal.query.filter(
            DataCheckDismissal.flight_id.in_(flight_scope),
            DataCheckDismissal.key.startswith("fee:"),
        )
    }
    history: dict[tuple[int, str, str], deque[tuple[date, bool | None]]] = defaultdict(
        deque
    )
    paid_counts: dict[tuple[int, str, str], int] = defaultdict(int)
    known_counts: dict[tuple[int, str, str], int] = defaultdict(int)
    findings = []
    for flight_date, day_flights in groupby(flights, key=lambda flight: flight.date):
        day_movements = []
        for flight in day_flights:
            for kind in KINDS:
                airfield = airfield_for(flight, kind)
                if not airfield or (kind == LANDING and flight.landing_count == 0):
                    continue
                key = (flight.aircraft_id, airfield, kind)
                observations = history[key]
                while observations and flight_date - observations[0][0] > HISTORY:
                    _, was_charged = observations.popleft()
                    paid_counts[key] -= was_charged is True
                    known_counts[key] -= was_charged is not None
                fee_status = status_by_flight[flight.id].get(kind)
                charged = fee_status is True
                no_fee = (
                    fee_status is False
                    or (flight.id, feedback_key(kind, airfield, "no_fee")) in feedback
                )
                day_movements.append(
                    (key, True if charged else False if no_fee else None)
                )
                if (
                    charged
                    or no_fee
                    or not start <= flight_date <= today - INVOICE_GRACE
                ):
                    continue
                paid, known = paid_counts[key], known_counts[key]
                # Unrecorded fees are unknown. Require enough reviewed history
                # before applying a paid pattern to a mostly unreviewed logbook.
                if paid < 3 or paid * 4 < known * 3 or known * 5 < len(observations):
                    continue
                deferred = feedback.get(
                    (flight.id, feedback_key(kind, airfield, "defer"))
                )
                if deferred is not None and today - deferred < DEFER_FOR:
                    continue
                findings.append(MissingFee(flight, kind, airfield, paid, known))
        for key, status in day_movements:
            history[key].append((flight_date, status))
            paid_counts[key] += status is True
            known_counts[key] += status is not None
    return list(reversed(findings))


def linkable_expenses(aircraft_ids: list[int], start: date, end: date) -> list[Expense]:
    return list(
        Expense.query.filter(
            Expense.aircraft_id.in_(aircraft_ids),
            Expense.flight_entry_id.is_(None),
            Expense.date.between(start, end),
            Expense.expense_type.in_((*FEE_TYPES, ExpenseType.OTHER)),
        )
        .order_by(Expense.date.desc(), Expense.id.desc())
        .all()
    )


def link_expense(expense: Expense, flight: Flight) -> bool:
    updated = db.session.execute(
        db.update(Expense)
        .where(Expense.id == expense.id, Expense.flight_entry_id.is_(None))
        .values(flight_entry_id=flight.id)
        .returning(Expense.id)
    )
    return updated.scalar_one_or_none() is not None


def record_feedback(flight_id: int, key: str, user_id: int | None) -> None:
    table = DataCheckDismissal.__table__
    insert = pg_insert if db.engine.dialect.name == "postgresql" else sqlite_insert
    values = {"dismissed_by_user_id": user_id, "created_at": datetime.now(UTC)}
    db.session.execute(
        insert(table)
        .values(flight_id=flight_id, key=key, **values)
        .on_conflict_do_update(
            index_elements=[table.c.flight_id, table.c.key], set_=values
        )
    )
