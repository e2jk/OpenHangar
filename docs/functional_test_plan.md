# Functional Test Plan — intent-based coverage

Prepared 2026-07-14 as a follow-up to the two earlier test-quality passes.
This document is an implementation-ready plan: it says exactly which
functional tests to add, how to build them, and in what order. Implement it
as written; deviate only with a documented reason.

**Where this fits.** The suite has been audited on two axes already:

1. *Line coverage* — 100 %, enforced by CI and the pre-push hook.
2. *Assertion strength* — [`test_quality_audit.md`](test_quality_audit.md)
   (2026-07-13): mutation-style spot checks on money/counter code; six
   boundary gaps hardened.

This plan is the third axis: **workflow / intent coverage**. Today's ~77
test files are feature-scoped and low-level: state is built by direct
`db.session.add(...)` (61 of 77 files), login is a session-key injection
(`sess["user_id"] = ...`), CSRF and rate limiting are globally disabled in
`tests/conftest.py`, and each file asserts one phase's routes in isolation.
That proves each unit does what it does — it does not prove the *product*
does what a user intends across features: that flying an aircraft advances
its maintenance clock, that a rental ends in a statement whose numbers
reconcile, that tenant B can never see tenant A's data on any route. Those
are the tests this plan adds.

## Audit summary — what is missing (the gap list)

| # | Gap | Evidence |
|---|-----|----------|
| G1 | No multi-feature journeys: nothing drives setup → aircraft → flight → dashboard, or reservation → dispatch → flight → charge → statement, through the HTTP surface end-to-end | every test file scoped to one phase/feature |
| G2 | State setup bypasses the product: fixtures write models directly, so route-level side effects (milestones, notifications, prefills, derived fields) are silently skipped in most scenarios | 61/77 files use `db.session.add` for setup |
| G3 | Cross-feature invariants untested: counter continuity across manual entry + import + edit; grounding snag ↔ reservation ↔ dispatch interplay; expense tiers ↔ cost dashboard figures | isolated files per feature |
| G4 | Tenant isolation is tested only where bugs were already found (the four gaps fixed in `06fe1b5`), not systematically over the route map | no test enumerates `app.url_map` |
| G5 | Role enforcement is spot-checked on "representative routes" (`test_multi_user.py` docstring), not as a role × route matrix | idem |
| G6 | Operating-model gating (`sole_pilot` … `flight_school`) has no dedicated behavioural suite: what each model's user can see/do is asserted only incidentally | grep `operating_model` in tests |
| G7 | Almost all tests bypass real authentication; CSRF-enabled behaviour is exercised in only 5 files; no journey covers invite → accept → login → TOTP → password change → old-credential rejection as one flow | conftest disables CSRF; `_login` injects session |
| G8 | Restore is verified structurally, not behaviourally: nothing proves the app is *usable* (login, pages, figures) after a backup → wipe → restore cycle | `test_backup.py` |
| G9 | Notification outcomes are unit-tested per check function; no test creates a due-soon world via the product and asserts exactly who gets which email when the daily loop runs | `test_notifications.py` |

## Conventions for all new functional tests

- **Location**: new directory `tests/functional/`, with its own
  `tests/functional/conftest.py`. Existing `tests/conftest.py` fixtures
  (`app`, `client`, `clean_db`) are inherited; do not modify them.
- **File naming**: after the journey, never the phase —
  `test_journey_rental_cycle.py`, not `test_phase37_functional.py`.
- **Drive the product, not the ORM.** Journeys create state through the
  same POSTs a browser would make. Direct model access is allowed only to
  (a) assert end-state, (b) plant things the UI cannot create (e.g. an
  expired invitation token, a past-dated document), and (c) time travel
  (backdating `created_at`/dates). Each direct write must carry a
  one-line comment saying why the route path can't be used.
- **Real login.** `tests/functional/conftest.py` provides
  `login(client, email, password)` that POSTs `/login` (two-step: password,
  then TOTP if enabled) and asserts it landed on the dashboard. No
  `sess["user_id"]` injection anywhere under `tests/functional/`.
- **A `submit()` helper**: POST + `follow_redirects=True`, assert the final
  status is 200 and no `alert-danger` flash is present (or, with
  `expect_error=True`, that one is). Most journey steps go through it —
  this is what catches "the form silently 200s but saves nothing".
- **Assert user-visible outcomes with hand-computed constants.** Expected
  figures are literals computed by hand in a comment, never re-derived with
  the same formula as the code (the tautology trap documented in
  `test_quality_audit.md`).
- **No mocking inside journeys**, with exactly two sanctioned exceptions:
  outbound email (`patch("services.email_service.send_email")`, the
  existing pattern from `test_notifications.py`) and network/tile fetches
  (already blocked by the autouse fixture). Never mock the DB, services,
  or clock — for time, backdate data instead.
- **One test = one journey**; multiple assertions along the way are
  expected and fine. Keep each journey under ~1 s (in-memory SQLite makes
  this easy); they run in the normal suite, no new markers, no xdist
  changes.
- **Coverage**: these tests overlap lines that unit tests already cover —
  that is fine and expected. The 100 % gate is unaffected.
- **Do not rewrite existing tests.** This plan is purely additive; the
  low-level suite stays as the fast, precise failure-localisation layer.

### Shared fixtures to build first (`tests/functional/conftest.py`)

- `owner_env` — via routes: POST `/setup` (wizard: admin account, tenant,
  operating model `sole_operator`), then create one aircraft with an engine
  component through the aircraft forms. Returns a small dataclass
  (`client`, `tenant_id`, `aircraft_id`, credentials).
- `second_user(env, role)` — invitation flow via routes: admin creates the
  invitation, the new user accepts it and logs in with a **fresh client**
  (two clients = two concurrent sessions; never share one client between
  actors in a journey).
- `log_flight(client, aircraft_id, **fields)` — POSTs the real flight form
  with sensible defaults; returns the created entry id (parsed from the
  redirect or looked up by date+route).
- Constants module with the hand-computed figures used across journeys.

---

## The journeys

Priority P1 = highest value (money + operational correctness), implement
first. Each entry lists: intent, steps, key assertions, and any existing
partial coverage the implementer must extend rather than duplicate.

### P1 — core correctness journeys

**J1 — First run to first flight** ✅ — `test_journey_first_flight.py`
*Intent: a fresh install can reach a working, correct instance without any
manual DB surgery.*
Steps: `/setup` wizard → create aircraft + engine component → log one
flight (counters 1000.0 → 1001.5) → open dashboard, aircraft detail,
airframe logbook.
Assert: dashboard and aircraft detail both show engine hours **1001.5**;
logbook lists the entry; the *next* flight form pre-fills counter starts
with 1001.5 (`_get_counter_hint` behaviour, asserted through the rendered
form, not the helper).
Existing partial coverage: `test_onboarding_wizard.py` (wizard alone),
`test_counters.py` (hint helper alone) — neither chains them.

**J2 — Counter continuity across entry paths** ✅ —
`test_journey_counter_continuity.py`
*Intent: no matter how entries enter the system, the logbook stays
arithmetically continuous and aircraft hours equal the highest counter.*
Steps: log flight A via the form; log flight B via the form (accept
prefill); edit flight A's end counter via `/flights/<id>/edit`; import a
third entry via the airframe CSV import; then read the logbook page and
aircraft detail.
Assert: rendered rows show the exact counter chain; aircraft current
hours = max end counter across all three paths; the edit did **not**
disturb flight B's stored start value (documents today's semantics — the
continuity-discrepancy backlog item builds on it).
Existing: `test_airframe_import.py`, `test_flights.py` cover each path
separately; nothing mixes them.

**J3 — Maintenance driven by flying** ✅ —
`test_journey_maintenance_lifecycle.py`
*Intent: maintenance status is a consequence of actually flying, and
servicing resets the cycle.*
Steps: create an hours trigger (due at 1010.0, interval 50) and a calendar
trigger via the maintenance forms; fly via the flight form until engine
hours reach 1009.0 (≥ 90 % → due soon), then past 1010.0 (overdue); mark
serviced via the route; also backdate the calendar trigger to overdue.
Assert: trigger status transitions **ok → due soon → overdue** appear on
the aircraft maintenance page and the fleet overview at each stage;
servicing advances due-hours to 1060.0 and status back to ok; statuses
were never touched by direct model writes.
Existing: `test_maintenance.py`/`test_fleet_maintenance.py` set counter
values directly; no test advances hours by logging flights.

**J4 — Full rental cycle, two real users** ✅ —
`test_journey_rental_cycle.py`
*Intent: the Phase 37 loop closes: authorize → reserve → check out → fly →
check in → charge → settle, with correct money at the end, each actor
using only their own permissions.*
Steps (owner client + renter client from `second_user`): owner sets rates
(e.g. 120.00/h wet, engine-time basis) and authorizes the renter; renter
creates a reservation; owner checks out (counters recorded); renter logs
the flight (1.5 h engine delta); owner checks in; draft charge appears;
owner finalizes, records a payment of 100.00; renter opens their account
page; owner exports the statement CSV.
Assert: draft = **180.00** (hand-computed); balance after payment =
**80.00**; statement CSV opening + charges − payments = closing exactly;
the renter's client gets 403/404 on owner-only pages (rate settings, other
accounts) *at every step it participates in*; the flight auto-linked to
the reservation.
Existing: `test_rental_charges.py` (39 tests) and `test_dispatch.py` cover
the pieces with direct-model setup and mostly single-actor requests; no
test runs the loop through HTTP as two users.

**J5 — Reservation guards interplay** ✅ —
`test_journey_reservation_guards.py`
*Intent: the calendar tells the truth: conflicts, downtime, and grounded
aircraft actually prevent/warn what they should, and resolution frees
things up.*
Steps: user A reserves; user B's overlapping attempt is rejected; owner
adds a `MaintenanceDowntime` window — reservation inside it rejected;
open a grounding snag → new reservation shows the warning (then flip the
tenant policy to block via the settings route → rejected); resolve the
snag → same reservation now succeeds; cancel A's reservation → B can book
the slot.
Assert: each outcome through response content (warning text vs rejection
vs success) and the calendar page rendering all three object types.
Existing: `test_availability_guards.py` covers each guard singly,
direct-model; the policy-flip and resolve-then-retry sequences are new.

**J6 — Expenses to cost dashboard** ✅ — `test_journey_cost_dashboard.py`
*Intent: the wet-rate figure an owner sees is the correct consequence of
the expenses and flights they entered.*
Steps: via routes — add a fixed expense (annual insurance 1200.00 covering
a known 12-month span), an operating expense (fuel 300.00), a per-flight
landing fee linked to a flight (must stay excluded), and 10.0 h of flights
inside the period; open `/aircraft/<id>/costs?period=…`.
Assert: fixed/hour = **120.00**, operating/hour = **30.00**, wet rate =
**150.00** rendered on the page (hand-computed literals); landing fee
absent from the rate; a second period with pro-rated fixed cost asserts
the pro-rating rule end-to-end.
Existing: `test_cost_dashboard.py` unit-tests `_compute_stats`-level maths;
figures-as-rendered after route-driven data entry are new.

### P2 — data-integrity journeys

**J7 — Pilot logbook totals from mixed sources** ✅ —
`test_journey_pilot_logbook_totals.py`
*Intent: the EASA totals row and currency panel reflect everything the
pilot logged, whatever the entry path.*
Steps: one flight via the unified flight form (role PIC, night 1.0,
landings 2); one standalone manual entry; one FSTD session; then read the
pilot logbook page and currency panel. Backdate the entries (sanctioned
direct write) so a 90-day passenger-currency boundary is crossed by the
*newest* flight.
Assert: totals row figures are the hand-computed sums per column; FSTD
time appears in the FSTD column only; passenger currency flips to OK
because of the flight logged through the form.
Existing: `test_pilot_logbook.py` and `test_pilot_currency.py` each cover
their half with direct writes.

**J8 — GPS import round trip** ✅ — `test_journey_gps_import.py`
*Intent: a GPS file becomes exactly one flight + one pilot entry + one
track, and importing it twice does not duplicate anything.*
Steps: upload a real fixture file through the import flow, accept the
review, then upload the same file again and follow the duplicate path
(link/discard).
Assert: after the second pass there is still exactly one `FlightEntry`,
one `PilotLogbookEntry`, one `GpsTrack`; the logbook page shows the track
link; counters/hours unaffected by the re-import.
Existing: `test_gps_import.py`/`test_pilot_gps_import.py` cover parsing
and single-pass import; the re-upload journey is new.

**J9 — Backup, wipe, restore, then *use the app*** ✅ —
`test_journey_backup_restore.py`
*Intent: a restore is proven by the product working afterwards, not by
table counts.*
Steps: build a small world via routes (J1 fixture + one document upload +
one expense); create an encrypted backup via the route; wipe the DB and
the upload dir; restore via the restore flow; then **log in again with the
original password** and re-read: dashboard hours, logbook entry, document
download (bytes identical), expense list.
Assert: every read matches the pre-backup state; login works (password
hashes survived); the uploaded file's content round-tripped.
Existing: `test_backup.py` asserts archive structure and row counts —
keep it; the post-restore usability pass is new.

**J10 — Notification day** ✅ — `test_journey_notification_day.py`
*Intent: when the daily loop runs, exactly the right people get exactly
the right emails, as a consequence of product state.*
Steps: via routes create — a document expiring within the threshold, an
hours trigger pushed to due-soon *by flying* (reuse J3 helper), and a
second user whose preference for one notification type is switched off via
the prefs UI. Run the daily dispatch with
`patch("services.email_service.send_email")`.
Assert: the mock's call list (recipients × notification types) matches the
expected set exactly — including the *absence* of the opted-out user; a
second run the same day sends nothing new (dedup behaviour).
Existing: `test_notifications.py` unit-tests each `_check_*`; the
state-built-via-product, exact-recipient-set assertion is new.

### P3 — security & matrix sweeps

**J11 — Cross-tenant isolation sweep** ✅ — `test_tenant_isolation_sweep.py`
*Intent: no route, present or future, leaks tenant B's objects to a
tenant-A user.*
Build two fully-populated tenants (reuse the P1/P2 fixtures twice). Then
enumerate `app.url_map` GET rules; for every rule with an `<int:...>`
converter, substitute tenant B's matching object id and request it as
tenant A's admin. Maintain one explicit table in the test mapping
converter-name → tenant-B id (`aircraft_id`, `flight_id`, `document_id`,
…); the test **fails on any unmapped int-converter rule**, so every new
route must be classified when it is added.
Assert: every response is 403/404 — and additionally that the body never
contains tenant B's registration/name markers (catches routes that 200
with leaked content).
Existing: `06fe1b5` fixed four such gaps found by hand; this turns that
one-off hunt into a permanent net. This is the highest-value single test
in the plan.

**J12 — Role × write-route matrix** ✅ — `test_role_write_matrix.py`
*Intent: the role table in `AGENTS.md` is enforced everywhere, not just on
"representative routes".*
One parametrized test: for each role (ADMIN, OWNER, PILOT, MAINTENANCE,
RENTER, VIEWER) × each state-changing endpoint (explicit list per
blueprint, with the expected allow/deny per role written as a literal
table in the test file). Six logged-in clients from the invitation flow.
Assert: exact status class per cell (2xx/3xx vs 403); the test fails if a
POST route exists in `app.url_map` that is absent from the table (same
forcing function as J11).
Existing: `test_multi_user.py`/`test_authorization.py` spot-check; the
exhaustive matrix with a completeness guard is new.

**J13 — Operating-model gating** ✅ — `test_operating_model_gating.py`
*Intent: each of the five operating models presents only its features.*
For each `OperatingModel`, run the setup wizard selecting it, then assert
per model: nav entries present/absent on the dashboard, and 2-4
model-inappropriate routes deny or hide (e.g. `sole_pilot`: no fleet
maintenance, no reservations; `flight_club`: reservations present).
Encode expectations as a literal table.
Existing: `test_onboarding_wizard.py` asserts wizard storage, not the
downstream gating.

**J14 — Credential lifecycle with CSRF on** ✅ —
`test_journey_credential_lifecycle.py`
*Intent: the full account lifecycle works as a user experiences it, with
the real CSRF machinery engaged.*
Module-scoped app override with `WTF_CSRF_ENABLED = True` (pattern exists
in `test_csrf.py`); every POST extracts the token from the rendered form.
Steps: admin invites → invitee accepts, sets password, logs in → enables
TOTP (pyotp) → logs out → logs in with password+TOTP → changes password →
asserts the old password now fails and an old session cookie is no longer
authenticated → password-reset flow via emailed token (mock send_email,
capture the link).
Existing: pieces across `test_multi_user.py`, `test_require_totp.py`,
`test_csrf.py`; the single chained flow is new.

### P4 — breadth (do these last)

- **J15 — Demo cycle** ✅ (`test_journey_demo_cycle.py`): enter demo → make a
  change in slot 1 → second visitor gets a different slot unaffected by
  slot 1's edits; demo data never bleeds into a real tenant. Extends
  `test_demo.py` with the two-visitor interplay.
- **J16 — Share-link lifecycle** ✅ (`test_journey_share_links.py`): create
  share link → anonymous client sees exactly the shared scope (and nothing
  else — probe 2-3 sibling objects) → revoke → 404. Extends
  `test_share.py` with the negative-scope probes.
- **J17 — Localised journey smoke** ✅ (`test_journey_localised.py`): rerun a
  trimmed J1 with the locale switched to `fr`, then `nl` (compile `.mo` in
  the fixture if absent): pages render in the right language (assert one
  known string per page), locale survives the whole journey, and a French
  page uses U+202F before `:` in at least one known label.
- **J18 — Instance-admin provisioning** ✅ (`test_journey_instance_admin.py`):
  super-admin provisions a second tenant → its admin logs in, completes
  setup → J11's isolation assertions hold between the two tenants created
  *this* way (provisioning path, not fixture path).
- **J19 — Offline sync loop** ✅ (after Phase 38 ships; coordinate with the
  session implementing it): snapshot via `GET /api/offline/aircraft/<id>/logbook`
  → concurrent edit via the normal form → sync with stale bases → assert
  the per-field conflict payload → resolve → assert final DB state. The
  Phase 38 sub-phase tests cover units; this chains them against the form.

## Delivery order

Four batches, each independently committable, full gate green each time
(tests, ruff, mypy, translations untouched, no migrations involved):

| Batch | Contents | New files | Status |
|---|---|---|---|
| A | `tests/functional/conftest.py` + J1, J2, J3 | 4 | ✅ done |
| B | J4, J5, J6 | 3 | ✅ done |
| C | J11, J12 (the two sweeps — highest security value) | 2 | ✅ done |
| D | J7–J10, J13, J14, then P4 as time allows | 6+ | ✅ done |

Batch C is deliberately early: the sweeps are cheap to write once the
two-tenant fixture exists and they protect everything else. If effort must
be cut, cut P4, never C.

## Explicitly out of scope

- Playwright/e2e suite (separate concern, has its own crawl).
- Rewriting or "upgrading" existing unit tests.
- Mutation testing (covered by `test_quality_audit.md`'s follow-up note).
- Load/performance testing.
