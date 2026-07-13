# Phase 37 — Rental Operations: Implementation Spec

Companion to the Phase 37 section in
[`implementation_plan.md`](implementation_plan.md) and to
[`billing_service_design.md`](billing_service_design.md). The plan says
*what*; this document fixes the *how* — data model, routes, decisions, and
delivery order — so implementation can proceed without re-opening design
questions. Where this spec makes a choice, the choice has been made
deliberately; deviate only with a documented reason.

**Read first:** `AGENTS.md` (working rules), the billing design doc, and the
Phase 22/26/34 sections of the implementation plan (reservations, tenant
profile, notifications — all shipped and built upon here).

---

## Delivery order

Six sub-phases, each independently committable, each green on the full gate
(tests at 100 % coverage, ruff, mypy, translations, migration chain) before
moving on:

| Step | Contents | Depends on |
|---|---|---|
| 37a | Billing core (`BillingAccount`, `LedgerEntry`, `BillingService`) | — |
| 37b | Rates & terms (`AircraftBookingSettings` extension) | — |
| 37c | Renter authorization + reservation guard + expiry notification | — |
| 37d | Reservation ↔ flight link + dispatch (check-out / check-in) | — |
| 37e | Rental charges, payments, statements, renter-facing view | 37a, 37b, 37d |
| 37f | Availability guards (grounded warning, downtime, notification) | — |

37a–37c and 37f are mutually independent; 37e is the integration step.

---

## 37a — Billing core

Implement exactly as specified in
[`billing_service_design.md`](billing_service_design.md). One migration for
both tables. No UI in this step; service + model tests only.

---

## 37b — Rates & terms

### Model — extend `AircraftBookingSettings`

```python
# New columns (one migration):
rate_basis        = db.Column(db.String(16), nullable=False, default="engine_time")
                    # "engine_time" | "flight_time" — which counter delta is billed
rate_type         = db.Column(db.String(8), nullable=False, default="wet")
                    # "wet" | "dry" — label + whether fuel credits apply
min_hours_per_day = db.Column(db.Numeric(4, 1), nullable=True)
                    # multi-day bookings: minimum billed hours per calendar day
```

`hourly_rate` (existing) keeps its meaning: EUR per billed hour.

### Behaviour

- **Booking-time estimate** (Phase 22 code in `reservations/routes.py`):
  `estimated_hours = max(wall-clock hours, chargeable_days × min_hours_per_day)`
  when `min_hours_per_day` is set.
  **`chargeable_days` = number of distinct calendar dates (tenant-local = UTC
  dates; the app runs in UTC) touched by [start_dt, end_dt)** — a booking
  from 14:00 to 11:00 next day touches 2 days.
- The booking settings form (`/aircraft/<id>/reservations/settings` area)
  gains the three fields with inline help text; `rate_type` renders as a
  wet/dry radio, `rate_basis` as a select.
- Reservation detail and the estimate line show the rate as e.g.
  "180.00 EUR/h wet (engine time)".

### Tests

- Estimate honours `min_hours_per_day` (single-day, multi-day, overnight
  2-day case above); unset → wall-clock behaviour unchanged.
- Defaults applied on existing rows after migration (`engine_time`/`wet`).
- Settings form round-trips all three fields; invalid values rejected.

---

## 37c — Renter authorization

### Model

```python
class RenterAuthorization(db.Model):
    __tablename__ = "renter_authorizations"

    id                 = db.Column(db.Integer, primary_key=True)
    tenant_id          = db.Column(..., db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    renter_user_id     = db.Column(..., db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    aircraft_id        = db.Column(..., db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=True)  # NULL = whole fleet
    authorized_by_id   = db.Column(..., db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    granted_on         = db.Column(db.Date, nullable=False)
    expires_on         = db.Column(db.Date, nullable=True)   # NULL = does not expire
    checkout_flight_on = db.Column(db.Date, nullable=True)
    licence_seen_on    = db.Column(db.Date, nullable=True)
    medical_valid_until = db.Column(db.Date, nullable=True)  # owner-entered, NOT read from PilotProfile
    notes              = db.Column(db.Text, nullable=True)
    revoked_at         = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at         = ...
```

An authorization is **valid** when `revoked_at IS NULL` and
(`expires_on IS NULL OR expires_on >= today`) and
(`medical_valid_until IS NULL OR medical_valid_until >= today`).
Helper: `RenterAuthorization.is_valid` property + a
`valid_for(user, aircraft)` query helper (fleet-wide row OR row for that
aircraft).

The signed rental agreement is a `Document` with a new
`renter_authorization_id` FK column (nullable, `ondelete="CASCADE"`) —
same pattern as the existing `expense_id` receipt link; carries no
`aircraft_id`, visible to the renter concerned and `is_owner` users.

**Privacy decision (do not revisit):** these are owner-entered verification
facts. The code must NOT read the renter's `PilotProfile` or their pilot
documents; the UI may show a static hint telling the *owner* where to look
if the renter has shared documents (Phase 27 already makes pilot-profile
documents visible to admins).

### Tenant policy

New `TenantProfile` column (same migration):

```python
rental_authorization_policy = db.Column(db.String(8), nullable=False, default="warn")
# "off" | "warn" | "block"
```

Enforced in the reservation-create POST (and confirm, for pre-existing
pendings) for users who are not `is_owner`:

- `off` — no check.
- `warn` — reservation is created; flash warning to the renter and include
  the fact in the `RESERVATION_REQUEST` notification to the owner.
- `block` — POST is rejected with a clear message; GET form shows the
  explanation up front.

Configurable on the Configuration → usage-profile section (owner/admin).

### UI

- **Configuration → Renters** (new page, `is_owner` only):
  list of authorizations (renter, scope, granted, expires, status badge
  valid/expiring/expired/revoked), add/edit form, revoke button
  (sets `revoked_at`; no hard delete).
- Route names: `config.renters_list`, `config.renter_add`,
  `config.renter_edit`, `config.renter_revoke` under `/config/renters/`.
- Agreement upload uses the existing document-upload machinery.

### Notification

`RENTER_AUTHORIZATION_EXPIRY` — new `NotificationType` constant (string
table, no enum migration): fires from the daily pass for `is_owner` users
when an authorization's `expires_on` or `medical_valid_until` is within
`threshold_days` (default 30, default ON). `has_content` guard: no
soon-expiring authorizations → no email. Template lists the renters and
dates. Add to `REQUIRED_CAPS` (`is_owner`) and the defaults table, and to
the preferences page grouping (reuse the "Aircraft status" category).

### Tests

- Validity matrix: revoked / date-expired / medical-expired / fleet-wide vs
  per-aircraft / valid.
- Policy enforcement: renter blocked under `block`, warned under `warn`,
  silent under `off`; owner exempt in all modes.
- Notification: fires at threshold, respects preference, content guard.
- Tenant isolation: renters/authorizations of another tenant are neither
  listed nor addressable by ID.

---

## 37d — Reservation ↔ flight link + dispatch

### Model

```python
# FlightEntry — new nullable FK (one migration with DispatchRecord):
reservation_id = db.Column(..., db.ForeignKey("reservations.id", ondelete="SET NULL"), nullable=True)


class DispatchRecord(db.Model):
    __tablename__ = "dispatch_records"

    id                    = db.Column(db.Integer, primary_key=True)
    reservation_id        = db.Column(..., db.ForeignKey("reservations.id", ondelete="CASCADE"),
                                      nullable=False, unique=True)  # one dispatch per reservation
    # Check-out:
    out_at                = db.Column(db.DateTime(timezone=True), nullable=True)
    out_by_id             = db.Column(..., db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    out_engine_counter    = db.Column(db.Numeric(8, 1), nullable=True)
    out_flight_counter    = db.Column(db.Numeric(8, 1), nullable=True)
    out_fuel_state        = db.Column(db.String(64), nullable=True)   # free text ("full", "40 L", …)
    out_walkaround_ok     = db.Column(db.Boolean, nullable=False, default=False)
    out_snags_acknowledged = db.Column(db.Boolean, nullable=False, default=False)
    # Check-in:
    in_at                 = db.Column(db.DateTime(timezone=True), nullable=True)
    in_by_id              = db.Column(..., db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    in_engine_counter     = db.Column(db.Numeric(8, 1), nullable=True)
    in_flight_counter     = db.Column(db.Numeric(8, 1), nullable=True)
    in_fuel_state         = db.Column(db.String(64), nullable=True)
    in_notes              = db.Column(db.Text, nullable=True)
```

### Behaviour

- **Flight pre-link:** on `/flights/new` POST for a managed aircraft, find a
  CONFIRMED reservation for (aircraft, logged-in pilot) whose
  [start_dt − 2 h, end_dt + 6 h] window contains the flight — set
  `reservation_id`. On the GET form, if such a reservation exists, show a
  small "will be linked to your reservation of …" notice. Never link across
  pilots. (The tolerance absorbs early departures and late returns; it is a
  constant in `flights/routes.py`, not configurable.)
- **Check-out** (`/reservations/<id>/checkout`, GET+POST): available to the
  reservation's pilot and `is_owner` from the reservation detail, from the
  start of the reservation day. Form pre-fills counters from the aircraft's
  latest flight-entry end values; requires both confirmation checkboxes;
  displays the open-snag list inline (the acknowledgement checkbox label
  references it). **Blocked when the aircraft is grounded** — `is_owner`
  may override via an explicit "dispatch anyway" checkbox, recorded in a
  dedicated `out_grounded_override` Boolean column (auditability matters —
  add the column to the model above).
- **Check-in** (`/reservations/<id>/checkin`, GET+POST): counters must be
  ≥ the check-out values; prompts "anything to report?" with a link to the
  snag form pre-filtered to the aircraft.
- **Discrepancy surfacing:** on the reservation detail page, compare the
  dispatch counter delta with the sum of linked flight-entry counter deltas;
  when they differ, show a warning alert naming both figures. No blocking.
- Reservation **cancellation is refused after check-out** (409-style flash).

### Tests

- Pre-link: inside window links; other pilot / other aircraft / outside
  window does not; notice rendered on GET.
- Check-out: pre-fill correct; checkboxes required; grounded blocks;
  owner override recorded; double check-out refused (unique constraint).
- Check-in: counter floor validation; check-in without check-out refused.
- Discrepancy warning appears iff deltas differ.
- Cancellation after check-out refused.
- Tenant isolation + permission: only the reservation pilot and owners can
  dispatch; other tenants' reservations 404.

---

## 37e — Rental charges & settlement

### Model

```python
class RentalCharge(db.Model):
    __tablename__ = "rental_charges"

    id              = db.Column(db.Integer, primary_key=True)
    reservation_id  = db.Column(..., db.ForeignKey("reservations.id", ondelete="CASCADE"),
                                nullable=False, unique=True)
    renter_user_id  = db.Column(..., db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status          = db.Column(db.String(12), nullable=False, default="draft")  # "draft" | "final"
    billable_hours  = db.Column(db.Numeric(6, 1), nullable=False)
    hourly_rate     = db.Column(db.Numeric(8, 2), nullable=False)   # snapshot at draft time
    rate_type       = db.Column(db.String(8), nullable=False)       # snapshot
    fuel_credit     = db.Column(db.Numeric(10, 2), nullable=False, default=0)  # positive number, subtracted
    adjustment      = db.Column(db.Numeric(10, 2), nullable=False, default=0)  # signed, owner note required if ≠ 0
    adjustment_note = db.Column(db.String(255), nullable=True)
    total           = db.Column(db.Numeric(10, 2), nullable=False)  # hours×rate − credit + adjustment
    finalized_at    = db.Column(db.DateTime(timezone=True), nullable=True)
    finalized_by_id = db.Column(..., db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at      = ...
```

### Drafting (automatic, at check-in)

On successful check-in, create the draft in the same transaction:

- `billable_hours = max(counter_delta, chargeable_days × min_hours_per_day)`
  where `counter_delta` uses the counter selected by `rate_basis`
  (fall back to the *other* counter if the selected one was left blank at
  dispatch, and note that in the draft view), and `chargeable_days` follows
  the 37b definition applied to the actual out/in timestamps.
- `hourly_rate` / `rate_type` snapshot from `AircraftBookingSettings`
  (missing rate → draft still created with rate 0 and a "no rate
  configured" warning badge; the owner fixes it before finalizing).
- **Fuel credit (wet rates only):** sum of `Expense` rows of type `fuel`
  linked (`flight_entry_id`) to the reservation's flights **and created by
  the renter**. Rendered as a pre-filled, owner-editable field on the draft
  — the automatic sum is a convenience, the owner's number is authoritative.
  Dry rates: field hidden, forced 0.

### Finalization & settlement

- Owner reviews the draft on the reservation detail (or the new renter
  account page), may edit hours / credit / adjustment (note required),
  then **Finalize** — sets status, stamps who/when, and posts to the ledger
  (37a): one `CHARGE` of `total` (`source_type="rental_charge"`).
  A finalized charge is immutable; corrections go through
  `BillingService.reverse` + a new adjustment entry, owner-only.
- **Payments:** owner records a payment against the renter's account
  (amount, date, note) → `PAYMENT` entry. No edit/delete; corrections by
  reversal.
- **Renter account pages:**
  - Owner view `/config/renters/<user_id>/account` — balance, entries,
    record-payment form, per-period statement + CSV download
    (`hx-boost="false"` on the CSV link — binary download rule).
  - Renter self view `/my/account` — own balance, entries, statements;
    no write actions. Navbar entry appears only when the user has a
    renter account with ≥ 1 entry.

### Tests

- Draft math: rate_basis selection, counter fallback, min-hours floor,
  fuel-credit sum (only renter's fuel expenses on linked flights; dry → 0).
- Finalize: ledger entry posted once (re-finalize refused); totals match;
  immutability (edit routes refuse `status="final"`).
- Payment/reversal flows; balance correctness.
- Statement CSV: header metadata, rows, opening/closing reconciliation.
- Permission matrix: renter sees only own account; owner sees all;
  cross-tenant 404s.

---

## 37f — Availability guards

### Grounded-aircraft reservations

New `TenantProfile` column (same migration as 37c's policy or standalone):

```python
grounded_reservation_policy = db.Column(db.String(8), nullable=False, default="warn")  # "warn" | "block"
```

Applied at reservation create + confirm when the aircraft has an unresolved
grounding snag: `warn` shows a prominent alert (creation proceeds);
`block` refuses for non-owners (owners always get warn-level at most —
they may be booking the aircraft *for* the shop visit).

### MaintenanceDowntime

```python
class MaintenanceDowntime(db.Model):
    __tablename__ = "maintenance_downtimes"

    id          = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(..., db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False)
    start_dt    = db.Column(db.DateTime(timezone=True), nullable=False)
    end_dt      = db.Column(db.DateTime(timezone=True), nullable=False)
    reason      = db.Column(db.String(255), nullable=True)
    created_by_id = db.Column(..., db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
```

- CRUD for `is_owner`/`is_maintenance` from the aircraft's reservations page
  ("Block period" button) and the maintenance section.
- Conflict detection: extend the Phase 22 overlap check so a downtime
  window rejects overlapping reservation creation/confirmation exactly like
  a CONFIRMED reservation (and vice versa: creating a downtime over
  existing confirmed reservations shows the conflicts and asks the owner to
  resolve them manually — it does not auto-cancel).
- Calendar rendering: distinct style (striped grey), label = reason.

### Grounding notification

`RESERVATION_AIRCRAFT_GROUNDED` — new `NotificationType`: when a grounding
snag is opened, notify pilots holding CONFIRMED reservations on that
aircraft with `end_dt` in the future (immediate-event pass, same hook as
`GROUNDING_SNAG_OPENED` which continues to serve owners/maintenance).
Default ON, any authenticated role, `has_content` guard drops it when no
future confirmed reservations exist. Email names the aircraft, the snag
title, and the affected reservation date(s).

### Tests

- Policy matrix (warn/block × renter/owner) on create and confirm.
- Downtime: overlap rejection both directions; calendar renders; CRUD
  permission (pilot cannot create); cross-tenant isolation.
- Notification: fires only for future confirmed reservations of that
  aircraft; cancelled/past excluded; preference respected.

---

## Cross-cutting requirements

- **Migrations**: every model change above names its migration; IDs via
  `python3 -c "import secrets; print(secrets.token_hex(6))"`; run
  `scripts/check_migrations.py` after each.
- **i18n**: every new UI string wrapped in `_()`; French (U+202F rules!) and
  Dutch added for each sub-phase; `scripts/check_translations.py` green.
- **JS**: any new interactive behaviour (counter pre-fill on dispatch,
  draft-charge live total) follows the module pattern — external file in
  `app/static/js/`, IIFE + `ohInited` guard + `htmx:afterSettle`, loaded
  from `base.html`. No inline `<script nonce>` in child templates.
  CSV/statement download links: `hx-boost="false"`.
- **Docs**: tick the Phase 37 checkboxes in `implementation_plan.md` per
  sub-phase; add a "Renting your aircraft" section with screenshots to
  `user-guide.md` at the end (manifest entries: renter list, dispatch form,
  renter account/statement); extend `docs/configuration.md` only if new env
  vars appear (none are planned — both policies are DB-backed tenant
  settings, not env vars).
- **Dev seed**: extend with — one renter with valid authorization + one
  expired; one completed cycle (confirmed reservation → dispatch out/in →
  finalized charge → partial payment, leaving a positive balance); one
  draft charge; one maintenance downtime next week; one future confirmed
  reservation on a grounded aircraft (for the notification test data).

## Decisions log (why, in one line each)

- **Generic ledger over per-phase balances** — three phases, one money core;
  reversal-based corrections make statements reproducible.
- **Owner-entered qualification facts** — reading `PilotProfile` would leak
  private data and be wrong for renters who don't use the logbook.
- **Draft charge at check-in, human finalization** — counters are facts,
  price is a judgment; never auto-bill.
- **Fuel-credit auto-sum is a suggestion, owner's figure authoritative** —
  receipts arrive late and in odd shapes; don't over-automate.
- **Policies on `TenantProfile`, not env vars** — per-tenant behaviour,
  changeable at runtime by the owner, no deploy needed.
- **Downtime ≠ grounding snag** — planned unavailability is scheduling
  (blocks bookings), grounding is airworthiness (blocks dispatch); they
  interact but are separate records.
