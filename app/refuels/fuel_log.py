"""Fuel log: every refuel of an aircraft, matched to its costs, and the
fuel figures derived from them.

A refuel is recorded either on a flight — fuel added before and/or after it
(Flight.fuel_added_before_*/fuel_added_after_*) — or as a standalone Refuel
(e.g. topping off during maintenance). Its cost lives in a separate fuel
Expense, created from the refuel's "Log as expense" link. That link keeps
the flight (flight_entry_id) for a flight's refuel and only the date for a
standalone one, so an expense is matched back to its refuel the same way:

* a flight's refuel ↔ a fuel expense linked to that flight, preferring the
  one with the same quantity and unit (a flight can have two refuels, each
  with its own expense);
* a standalone refuel ↔ a fuel expense with no flight, on the same date,
  again preferring the same quantity.

Each expense covers at most one refuel. Pure functions over model objects,
so the matching and the figures can be unit-tested without a request.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from models import (  # pyright: ignore[reportMissingImports]
    Expense,
    ExpenseType,
    Flight,
    Refuel,
)

# US gallon — what "gal" means on the forms (avgas/jet fuel are sold by the
# US gallon); only used to add gallons and litres into one litre total.
LITRES_PER_GALLON = 3.785411784

BEFORE = "before"
AFTER = "after"
STANDALONE = "standalone"


def to_litres(quantity: Any, unit: str | None) -> float:
    value = float(quantity)
    return value * LITRES_PER_GALLON if (unit or "L") == "gal" else value


@dataclass
class FuelEvent:
    """One refuel: when, how much, where it's recorded, and its cost."""

    date: date
    kind: str  # BEFORE / AFTER (a flight's refuel) or STANDALONE
    quantity: float
    unit: str
    flight: Any = None  # Flight, for BEFORE/AFTER
    refuel: Any = None  # Refuel, for STANDALONE
    expense: Any = None  # the matched fuel Expense, if any

    @property
    def litres(self) -> float:
        return to_litres(self.quantity, self.unit)

    @property
    def price_per_litre(self) -> float | None:
        if self.expense is None or not self.litres:
            return None
        return float(self.expense.amount) / self.litres


def _same_quantity(expense: Any, quantity: float, unit: str) -> bool:
    return (
        expense.quantity is not None
        and (expense.unit or "L") == unit
        and abs(float(expense.quantity) - quantity) < 0.005
    )


def _match(events: list[FuelEvent], expenses: list[Any]) -> None:
    """Give each event at most one of *expenses*, each expense used once:
    same quantity first, then whatever is left, in order."""
    pool = list(expenses)
    for event in events:
        exact = next(
            (e for e in pool if _same_quantity(e, event.quantity, event.unit)), None
        )
        if exact is not None:
            event.expense = exact
            pool.remove(exact)
    for event in events:
        if event.expense is None and pool:
            event.expense = pool.pop(0)


def build_fuel_log(
    aircraft_id: int, period_start: date | None, period_end: date
) -> list[FuelEvent]:
    """Every refuel of the aircraft in [period_start, period_end] (start
    None = all time), newest first, each matched to its fuel expense."""
    flight_query = Flight.query.filter(
        Flight.aircraft_id == aircraft_id,
        Flight.date <= period_end,
        (Flight.fuel_added_before_qty.isnot(None))
        | (Flight.fuel_added_after_qty.isnot(None)),
    )
    refuel_query = Refuel.query.filter(
        Refuel.aircraft_id == aircraft_id, Refuel.date <= period_end
    )
    if period_start is not None:
        flight_query = flight_query.filter(Flight.date >= period_start)
        refuel_query = refuel_query.filter(Refuel.date >= period_start)

    fuel_expenses = Expense.query.filter_by(
        aircraft_id=aircraft_id, expense_type=ExpenseType.FUEL
    ).order_by(Expense.date, Expense.id)
    by_flight: dict[int, list[Any]] = {}
    by_date: dict[date, list[Any]] = {}
    for expense in fuel_expenses:
        if expense.flight_entry_id is not None:
            by_flight.setdefault(expense.flight_entry_id, []).append(expense)
        else:
            by_date.setdefault(expense.date, []).append(expense)

    events: list[FuelEvent] = []
    for flight in flight_query.all():
        flight_events = [
            FuelEvent(flight.date, kind, float(qty), unit or "L", flight=flight)
            for kind, qty, unit in (
                (BEFORE, flight.fuel_added_before_qty, flight.fuel_added_before_unit),
                (AFTER, flight.fuel_added_after_qty, flight.fuel_added_after_unit),
            )
            if qty is not None
        ]
        _match(flight_events, by_flight.get(flight.id, []))
        events.extend(flight_events)

    standalone_by_date: dict[date, list[FuelEvent]] = {}
    for refuel in refuel_query.order_by(Refuel.id).all():
        event = FuelEvent(
            refuel.date,
            STANDALONE,
            float(refuel.quantity),
            refuel.unit or "L",
            refuel=refuel,
        )
        standalone_by_date.setdefault(refuel.date, []).append(event)
        events.append(event)
    for day, day_events in standalone_by_date.items():
        _match(day_events, by_date.get(day, []))

    def _sort_key(e: FuelEvent) -> tuple[Any, ...]:
        clock = None
        if e.flight is not None:
            clock = e.flight.departure_time or e.flight.takeoff_time
        # Within a day: the flight's "after" refuel follows its "before" one.
        return (e.date, clock is not None, clock or 0, e.kind == AFTER)

    events.sort(key=_sort_key, reverse=True)
    return events


@dataclass
class FuelSummary:
    litres_added: float = 0.0
    refuel_count: int = 0
    costed_count: int = 0
    # Fuel expenses dated in the period, per currency.
    cost_by_currency: dict[str, float] = field(default_factory=dict)
    # Only with a single currency: ratios across currencies are meaningless.
    currency: str | None = None
    cost: float | None = None
    price_per_litre: float | None = None
    engine_hours: float = 0.0
    flight_hours: float = 0.0
    flight_count: int = 0
    litres_per_engine_hour: float | None = None
    cost_per_engine_hour: float | None = None
    cost_per_flight_hour: float | None = None
    cost_per_flight: float | None = None


def _ratio(numerator: float | None, denominator: float) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def summarize(
    events: Iterable[FuelEvent],
    fuel_expenses: Iterable[Any],
    flights: Iterable[Any],
) -> FuelSummary:
    """Fuel figures for a period: *events* (its refuels), *fuel_expenses*
    (fuel expenses dated in it) and *flights* (its flights).

    Litres per engine hour approximates fuel burn — refuels don't line up
    exactly with a period's start and end, so it's meaningful over months,
    not a single flight. A flight counts towards cost per flight when it
    logged any engine or flight time."""
    s = FuelSummary()
    for event in events:
        s.refuel_count += 1
        s.litres_added += event.litres
        if event.expense is not None:
            s.costed_count += 1

    priced_amount = 0.0
    priced_litres = 0.0
    for expense in fuel_expenses:
        currency = expense.currency or "EUR"
        s.cost_by_currency[currency] = s.cost_by_currency.get(currency, 0.0) + float(
            expense.amount
        )
        if expense.quantity is not None and float(expense.quantity) > 0:
            priced_amount += float(expense.amount)
            priced_litres += to_litres(expense.quantity, expense.unit)

    for flight in flights:
        engine = _hours(
            flight.engine_time,
            flight.engine_time_counter_start,
            flight.engine_time_counter_end,
        )
        airborne = _hours(
            flight.flight_time,
            flight.flight_time_counter_start,
            flight.flight_time_counter_end,
        )
        s.engine_hours += engine or 0.0
        s.flight_hours += airborne or 0.0
        if engine or airborne:
            s.flight_count += 1

    if len(s.cost_by_currency) == 1:
        ((s.currency, s.cost),) = s.cost_by_currency.items()
        s.price_per_litre = _ratio(priced_amount, priced_litres)
        s.cost_per_engine_hour = _ratio(s.cost, s.engine_hours)
        s.cost_per_flight_hour = _ratio(s.cost, s.flight_hours)
        s.cost_per_flight = _ratio(s.cost, s.flight_count)
    s.litres_per_engine_hour = _ratio(s.litres_added, s.engine_hours)
    return s


def _hours(logged: Any, start: Any, end: Any) -> float | None:
    """A flight's time: the logged figure, else the counter difference."""
    if logged is not None:
        return float(logged)
    if start is not None and end is not None:
        return float(end) - float(start)
    return None


@dataclass
class FuelOnBoard:
    """The fuel in the aircraft's tanks now, as far as the logs tell."""

    liters: float
    flight: Any  # the flight whose fuel remaining it starts from
    added_after: float = 0.0  # added since: after that flight + standalone
    by_tank: list[dict[str, Any]] | None = None  # only when nothing added since
    capacity: float | None = None
    # Later flights exist without a fuel remaining figure: the value is the
    # last known one, not necessarily what's in the tanks now.
    later_flights: int = 0

    @property
    def is_current(self) -> bool:
        return self.later_flights == 0


def current_fuel_on_board(aircraft: Any) -> FuelOnBoard | None:
    """Fuel on board now: the fuel remaining at the end of the latest flight
    that recorded it, plus fuel added after that flight and standalone
    refuels since — capped at the tank capacity. None when no flight has
    recorded fuel remaining. Fuel added before a flight is already counted
    in that flight's fuel remaining, so only later additions count."""
    ordered = (
        Flight.date.desc(),
        Flight.departure_time.desc().nullslast(),
        Flight.takeoff_time.desc().nullslast(),
        Flight.id.desc(),
    )
    flights = Flight.query.filter_by(aircraft_id=aircraft.id)
    base = (
        flights.filter(Flight.fuel_remaining_qty.isnot(None)).order_by(*ordered).first()
    )
    if base is None:
        return None
    later = sum(
        1
        for f in flights.filter(Flight.date >= base.date).order_by(*ordered)
        if (f.date, f.id) != (base.date, base.id) and _is_after(f, base)
    )
    added = 0.0
    if base.fuel_added_after_qty is not None:
        added += to_litres(base.fuel_added_after_qty, base.fuel_added_after_unit)
    for refuel in Refuel.query.filter(
        Refuel.aircraft_id == aircraft.id, Refuel.date >= base.date
    ):
        same_day = refuel.date == base.date
        if not same_day or _created_after(refuel, base):
            added += to_litres(refuel.quantity, refuel.unit)
    capacity = sum(float(t.capacity_liters or 0) for t in aircraft.fuel_tanks) or None
    liters = float(base.fuel_remaining_qty) + added
    if capacity is not None:
        liters = min(liters, capacity)
    return FuelOnBoard(
        liters=round(liters, 1),
        flight=base,
        added_after=round(added, 1),
        by_tank=base.fuel_remaining_by_tank if not added else None,
        capacity=capacity,
        later_flights=later,
    )


def _clock(f: Any) -> Any:
    return f.departure_time or f.takeoff_time


def _is_after(f: Any, base: Any) -> bool:
    """Whether flight *f* comes after *base* (same order as the query)."""
    if f.date != base.date:
        return bool(f.date > base.date)
    a, b = _clock(f), _clock(base)
    if a is not None and b is not None and a != b:
        return bool(a > b)
    return bool(f.id > base.id)


def _created_after(refuel: Any, flight: Any) -> bool:
    """A standalone refuel on the flight's own day counts as after it when
    it was logged after the flight was — the day alone can't tell."""
    return bool(refuel.created_at > flight.created_at)
