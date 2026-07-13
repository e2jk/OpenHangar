# Test-suite quality audit

Prepared 2026-07-13/14 as engineering-process backlog item 4 of 4 (see
[`backlog.md`](backlog.md)). 100% line coverage is enforced by CI, but line
coverage only proves a line executed, not that its result was actually
verified. This is a mutation-style spot check (mentally flip a comparison
operator, a rounding call, a sign — would any existing assertion fail?),
focused on money/counter/rounding-adjacent code first per the backlog item,
since that's what the upcoming billing service will build on. It's a
snapshot, not a live document — re-derive from the code rather than trusting
line numbers here once the codebase has moved on.

## Findings hardened (tests added, no production code changed)

1. **`app/flights/routes.py:1066-1069`** — the tach-only flight-time
   calculation (`(engine_end - engine_start) - flight_counter_offset`,
   floored at 0.0) only had one test, and it used `flight_counter_offset =
   0.0`. A mutation deleting the offset subtraction entirely, or removing
   the `max(0.0, ...)` floor, passed unnoticed. Added
   `test_tach_only_subtracts_nonzero_flight_counter_offset` (nonzero offset,
   exact expected result) and `test_tach_only_floors_flight_time_at_zero`
   (offset exceeds the raw diff, expects exactly `0.0`, not negative).

2. **`app/services/component_limits.py:64-74`** — the `overdue`/`due_soon`/
   `ok` status thresholds use `<=` throughout, but no test hit an exact
   boundary (`tbo_remaining == 0`, `tbo_remaining == tbo * 0.1`,
   `limit_date == today`, `limit_date == today + 90 days`) — a `<=`↔`<`
   mutation on any of the four comparisons would have passed. Added
   `test_tbo_exact_boundaries` and extended `test_calendar_limit_windows`
   with the `today` and `today + 90 days` rows.

3. **`app/expenses/cost_dashboard.py:26`** — `resolve_period()`'s day count
   is `round(period_months * 365 / 12)`; for the 6-month option this is
   `182.5` exactly, a genuine rounding tie where Python's banker's rounding
   gives 182, not the "obvious" 183. No test ever called
   `resolve_period(6, ...)`, so a switch to a different rounding scheme
   would have passed. Added `test_6_months_rounds_half_to_even_not_up`.

4. **`app/expenses/routes.py:76,95` (`_compute_stats`)** — `total_cost` was
   never independently asserted anywhere in the suite (only the derived,
   pre-rounded `cost_per_hour` was ever checked, and only at round numbers).
   The `cost_per_hour is None` branch (`total_hours == 0`) was exercised by
   one test but only asserted `status_code == 200`, so `else None` silently
   becoming `else 0.0` would have passed. Added
   `test_compute_stats_total_cost_and_cost_per_hour_exact_values` (two
   non-round expense amounts, hand-calculated sum and rate) and
   `test_compute_stats_cost_per_hour_none_when_no_flight_hours` (asserts
   `is None`, not just falsy).

5. **`app/reservations/routes.py:159-166` (`_compute_cost`)** — every
   existing cost test used a rate/duration pair landing on an exact integer
   (100 × 2h = 200), so a rounding-precision regression (`round(x, 1)`
   instead of `round(x, 2)`, or dropping rounding entirely) would have
   passed undetected. Added
   `test_hourly_rate_with_fractional_duration_rounds_correctly` (87.5/h ×
   1.5h = 131.25, a genuine fractional-cent result) and
   `test_settings_no_divergence_warning_at_exact_10_percent` (pins the
   `<=` boundary on `RATE_DIVERGENCE_WARN_PCT`, previously only tested at
   5% and 100%).

6. **`app/services/recurring_expense_service.py:74,76`** — the "is this
   occurrence due yet" (`next_date > today`) and "has recurrence ended"
   (`next_date > recurrence_end`) checks were never exercised at the exact
   boundary (`next_date == today`, `next_date == recurrence_end`); a `>`↔`>=`
   mutation on either line would silently defer or drop an occurrence with
   no test failing. Added `test_occurrence_due_exactly_today_is_created` and
   `test_occurrence_exactly_on_recurrence_end_is_created`.

## Checked, no action needed

- **Over-mocking**: none of the four money-critical test files
  (`test_expenses.py`, `test_cost_dashboard.py`,
  `test_component_life_limits.py`, `test_recurring_expenses.py`) mock
  anything — all exercise real DB queries and real calculation functions.
  The mocks that do exist near this code (notification dispatch in
  `test_reservations.py`, GPS map-tile rendering in `test_flights.py`) stub
  genuine external/cosmetic boundaries, not the logic under test.

## Lower-priority weak-assertion smells (not hardened — logged for awareness)

These indicate under-verified page-rendering behavior rather than
money-calculation risk, so they were left as-is rather than expanded here:

- `tests/test_expenses.py` — `test_list_renders`, `test_list_shows_expense`,
  and `test_list_all_time` check status code / text presence but never a
  specific computed figure (now covered indirectly by the `_compute_stats`
  unit tests added above, which check the underlying function directly).
- `tests/test_cost_dashboard.py` — the two "falls back to default period"
  tests assert only the absence of a crash, not that the fallback period is
  actually `DEFAULT_PERIOD_MONTHS` (e.g. via a rendered figure that would
  differ under a wrong fallback).
- `tests/test_cost_dashboard.py::test_3_months_is_proportional` — its
  expected value is `round(3 * 365 / 12)`, i.e. it mirrors the
  implementation rather than a hand-calculated constant; the new 6-month
  test above uses a hardcoded `182` specifically to avoid this tautology
  for at least the tie-breaking case.

## Out of scope for this pass

A full mutation-testing run (`mutmut`/`cosmic-ray` or similar) over the
whole codebase was not attempted — this was a targeted, manual spot check
of the money/counter-adjacent code the backlog item called out first.
Priority 3 (broader weak-assertion sweep beyond expenses/reservations/
cost-dashboard) was similarly not exhaustive. Worth revisiting once the
billing service (Phase 37+) lands and there's new money-adjacent code to
include in the same pass.
