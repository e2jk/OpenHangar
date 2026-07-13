# Architecture / tech-debt review — before the Phase 37+ feature wave

Prepared 2026-07-13 as engineering-process backlog item 3 of 4 (see
[`backlog.md`](backlog.md)). Purpose: identify refactors worth landing
*before* Phases 37–40 (rental operations + shared billing ledger, see
[`phase37_rental_spec.md`](phase37_rental_spec.md) and
[`billing_service_design.md`](billing_service_design.md)) pile onto the
current structure, ranked by risk-reduction per effort. This is a snapshot,
not a live document — re-derive from the code rather than trusting line
numbers here once the codebase has moved on.

---

## 1. HIGH priority — consolidate authorization before adding billing actions

Four independent, partially-overlapping permission mechanisms coexist today:

1. **`@require_role(Role.X, Role.Y)`** (`app/utils.py`) — coarse role gate.
   73 call sites across 10 blueprint files. Each blueprint independently
   redeclares its own role-tuple constants — `_OWNER_ROLES = (Role.ADMIN,
   Role.OWNER)` is defined verbatim in at least 6 files (`aircraft`,
   `expenses`, `share`, `documents`, `airworthiness`, `reservations`)
   instead of imported from one place.
2. **`AuthorizationService.effective_mask()` / `.can()`**
   (`app/services/authorization.py`, built in Phase 23) — fine-grained
   per-aircraft permission-bitmask resolution, fully implemented and
   documented in [`access-control.md`](access-control.md). `.can()` has
   **zero external callers** anywhere in the codebase. Only
   `maintenance_view_level()` is actually called, and only from one route
   (`app/maintenance/routes.py:220`).
3. **`user_can_access_aircraft()` / `accessible_aircraft()`**
   (`app/utils.py`) — a third resolver that re-implements the same
   "does an access row exist for this user/aircraft" logic as #2, but as a
   boolean (row exists?) instead of a bitmask (which specific bits?). This
   is what most routes actually call, via the per-blueprint
   `_get_aircraft_or_404()` helpers.
4. **Raw `PermissionBit` bit-twiddling** directly in the permission-editor
   route (`app/users/routes.py:490-580`), which builds/parses the UI for
   masks that mechanism #2 is supposed to own but #3 mostly ignores.

### Confirmed concrete consequence

`PermissionBit.ROLE_DEFAULTS["maintenance"]` (`app/models.py:53-57`)
grants the maintenance role `EDIT_AIRCRAFT | EDIT_COMPONENTS` by design —
and [`access-control.md`](access-control.md)'s permission matrix correctly
documents this. But `edit_aircraft` (`app/aircraft/routes.py:320-321`) and
`edit_component` (`app/aircraft/routes.py:577-578`) are both gated
`@require_role(Role.ADMIN, Role.OWNER)` only — mechanism #1, which never
consults `PermissionBit` at all. A maintenance-role user is turned away by
the decorator before the fine-grained mask mechanism #2 (which would have
granted them access) ever runs. This isn't a security hole — it's *more*
restrictive than the designed intent — but it's a real functional gap: the
permission-editor UI (mechanism #4) lets an owner grant a maintenance user
`edit_components`, and that grant is silently inert for this route. Worth a
product decision (intentional restriction, tighten the docs/model instead?
or a bug, wire the route to the mask?) — flagging to the maintainer rather
than resolving unilaterally here, since it's a role-permission behavior
change either way.

### Why this matters for Phase 37/billing specifically

The billing spec will need new fine-grained actions with real consequence
if under- or over-scoped — "who can void an invoice," "who can adjust a
rental rate," "who can see another member's balance." Building those on
top of a system with four overlapping mechanisms (one of them provably
inert for at least one route) means either perpetuating the confusion with
a 7th file's worth of ad hoc role tuples, or being the *first* real caller
of `AuthorizationService.can()` in isolation — leaving the split
permanently half-migrated either way.

### Recommended action (bounded — not a full migration)

- For Phase 37/billing routes specifically: gate money-adjacent actions
  through `AuthorizationService.can()` from the first commit, not new
  `require_role` tuples. This is exactly the case fine-grained bits were
  built for, and it's the cheapest way to make `.can()` a real, tested
  code path instead of dead code.
- De-duplicate the repeated `_OWNER_ROLES = (Role.ADMIN, Role.OWNER)` (and
  similar) tuples into one shared constant, e.g. in `app/utils.py` next to
  `require_role` itself. Mechanical, low-risk, removes a divergence hazard.
- Flag the maintenance/edit_aircraft/edit_components gap above to the
  maintainer for a product decision.
- **Do not** attempt the full 73-call-site migration off `require_role`
  now — large surface, not blocking for Phase 37, and each call site needs
  its own test-coverage check. Track as a separate follow-up item.

---

## 2. MEDIUM priority — establish a service-layer convention before writing billing code

`app/services/` already holds 7 single-responsibility modules (auth,
advisory-lock, component-limits, email, notifications, recurring-expense
materialization, version-check, backup-scheduling) that follow a
consistent shape: narrowly scoped, lazily imported from inside route
functions to avoid import cycles (see the explicit comment in
`app/services/version_service.py:3-6`). That convention is *not*
consistently followed in the largest route handlers today:

- `app/flights/routes.py:_handle_log_flight_post()` (~273 lines) — GPS
  parse branch, aircraft resolution, ~20 raw form fields parsed inline,
  multi-table writes, all in one procedural function.
- `app/aircraft/routes.py:gps_import_confirm_one()` (~185 lines) and
  `app/pilots/routes.py:pilot_gps_import_confirm_one()` (~282 lines) — two
  *separate* implementations of "confirm one GPS segment → create a
  FlightEntry," one per blueprint, rather than one shared service function.
- `app/pilots/routes.py:import_execute()` (~218 lines) and
  `app/documents/routes.py:scan_documents()` (~129 lines) — CSV/filesystem
  parsing, validation, and persistence interleaved in the view function.

None of this is broken — it's covered by tests today — but the shared
billing ledger (per `billing_service_design.md`) is exactly the kind of
money-adjacent, multi-step, multi-table logic that has historically ended
up inline in a routes.py file in this codebase. Landing it inline would
make [item 4's](backlog.md) test-quality concerns (weak assertions around
money-adjacent code) harder to address later, not easier.

**Recommended action**: build the billing service as its own
`app/services/billing_service.py` module from the start (the design doc
already implies this shape), following the existing lazy-import
convention. Don't extract the *existing* GPS-import duplication or the
other inline handlers listed above as part of this prep work — real
duplication, but not billing-blocking. Track as a separate cleanup.

---

## 3. LOW priority — do not do yet: splitting `app/models.py`

`app/models.py` is 1953 lines / ~60 classes. It groups reasonably into
domain clusters (tenant/user/auth, aircraft/fleet, flights/logbook,
maintenance, expenses, documents, reservations/W&B, airworthiness,
notifications), but five models — `Aircraft`, `User`, `Document`,
`Component`, and `Expense` — have `relationship()`/`back_populates` edges
reaching into three or more of those clusters (`Aircraft` alone touches
13 other classes). A split is *mechanically* possible — SQLAlchemy already
resolves `db.relationship("ClassName", ...)` by string name, not by import
order — but small constant/enum classes (`DocCategory`, `CrewRole`,
`ExpenseType`, etc.) are physically interleaved between the model classes
that use them, so a clean split means moving ~15 small classes as well as
the ~10 large ones, and getting import order right for a "core" module
that `Aircraft`, `User`, `Document` would all need.

**Why this stays on the "do not do yet" list**: the billing phase will
*add* models (ledger entries, invoices, rate cards per
`billing_service_design.md`) rather than touch existing ones, so a split
isn't a prerequisite for that work. The effort (careful multi-file
refactor of the most actively-referenced file in the codebase, high
regression surface even with 100% coverage as a safety net) doesn't buy
risk-reduction proportional to Phase 37's actual needs. Revisit if the
file crosses roughly 3000 lines or the day-to-day pain of navigating one
file becomes a recurring complaint — not preemptively.

---

## Do-not-do-yet list (recap)

- Full `require_role` → `AuthorizationService.can()` migration across all
  73 call sites. (§1 — scope new code only, not existing.)
- Extracting the duplicated GPS-import-confirm-one logic
  (`aircraft` vs `pilots` blueprints) into a shared service. (§2 — real
  debt, not billing-blocking.)
- Splitting `app/models.py` into multiple modules. (§3.)
