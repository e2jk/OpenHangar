# Phase 39 — Shared Ownership: Implementation Spec

Status: **authoritative design** for Phase 39. The checklist in
[`implementation_plan.md`](implementation_plan.md) tracks delivery; this
document defines *how* each item is built. Where the two disagree, this
spec wins. Delivery is split into packages **39a–39h**, each independently
committable with the full gate green (ruff, mypy, migrations check,
translations check, 100 % coverage).

Read before starting:

- [`billing_service_design.md`](billing_service_design.md) — the shared
  ledger core. **It is already fully implemented** (`app/services/billing.py`,
  `BillingAccount` / `LedgerEntry` in `app/models.py`, built by Phase 37).
  Phase 39 posts entries to it; it must not be modified except where this
  spec explicitly says so (it doesn't).
- Phase 37's renter billing code — the direct precedent to mirror:
  - admin side: `config.renter_account`, `config.renter_record_payment`,
    `config.renter_statement_csv` in `app/config/routes.py` +
    `app/templates/config/renter_account.html`
  - self-service side: `reservations.my_account`,
    `reservations.my_account_statement_csv` in `app/reservations/routes.py`
- AOPA financial model (share-split fixed costs, usage-based operating
  costs) is summarised in the Phase 39 section of the implementation plan;
  no further reading needed.

---

## Motivating use-case

Three people jointly own one aircraft: Alice 50 %, Bob 30 %, Carol 20 %.
The annual insurance premium (a **fixed** cost) is split 50/30/20. In July
Bob flies 4.2 h; those hours are charged to Bob alone at the aircraft's
co-owner hourly rate — Alice and Carol owe nothing for them (**operating**
cost). Each co-owner has a capital account that starts at their buy-in,
goes down as costs are charged, and goes up when they transfer money to
the group. The managing owner sees everyone's balance on one dashboard,
records payments, flags whoever has been in the red for over a month, and
exports a per-owner statement at year end.

---

## Architecture in one paragraph

Phase 39 adds **one source-of-truth table** (`AircraftOwner`: who owns what
share of which aircraft), **one derived-data pass**
(`app/services/co_owner_billing.py`: idempotently converts fixed expenses,
flights, and buy-ins into `LedgerEntry` rows on each co-owner's
aircraft-scoped `BillingAccount`), and **read-only views over the ledger**
(dashboard, statements, valuation snapshots). All money movement goes
through the existing `BillingService`; Phase 39 never inserts
`LedgerEntry` rows directly and never adds an edit/delete route for them.
The capital-account display is simply the *negated* ledger balance
(ledger positive = owes money; capital positive = in credit).

---

## Hard constraints (do not violate)

1. **Ledger is append-only.** All writes via `BillingService.post()` /
   `BillingService.reverse()`. No update/delete routes for `LedgerEntry`,
   ever. Corrections = reversal + repost.
2. **`Decimal` end to end.** Never float. `BillingService` already
   quantizes to 2 dp with `ROUND_HALF_UP` at posting time.
3. **Sign convention** (from `billing_service_design.md`): positive ledger
   amount = the holder owes more. Therefore:
   - buy-in posts as `OPENING` with a **negative** amount (holder in credit),
   - fixed shares and flight-usage charges post as `CHARGE` (positive),
   - payments post as `PAYMENT` (negative),
   - **capital balance shown in UI = `-BillingService.balance(account)`**.
4. **Migrations**: every `models.py` change ships an Alembic migration,
   revision ID via `python3 -c "import secrets; print(secrets.token_hex(6))"`.
   Never touch an existing migration.
5. **i18n**: every UI string wrapped in `_()`, translated in `fr` and `nl`
   (French: U+202F before `: ; ! ? »` etc.). Ledger `description` strings
   follow the Phase 37 precedent: `str(_("…", …))` at posting time (frozen
   in the poster's locale; entries posted by the daily pass render in the
   default locale — accepted, same as Phase 37).
6. **Templates**: no `<script nonce>` in child templates. This phase needs
   **no JavaScript at all** — the owners form validates server-side; do not
   add a JS file. (A live share-sum indicator is explicitly out of scope.)
7. **Tenant isolation**: every route resolves the aircraft via the current
   tenant and 404s otherwise, mirroring the existing aircraft routes.
8. **100 % line coverage**, test files named after the feature
   (`test_shared_ownership_*.py`, never `test_phase39*.py`).
9. Tests run via `bash scripts/run-tests-with-coverage.sh` after
   ruff → format → mypy, in that order.

---

## Feature gating

**Non-negotiable requirement: on an instance whose operating model is NOT
`shared_ownership` and which has never defined co-owners (e.g. a
sole-operator or sole-pilot instance), this phase must leave zero visible
or behavioural trace.** (The only exception, a tenant that *used* the
feature and later switched its operating model away, is handled by the
legacy escape hatch in the helper below.) Concretely:

- no nav links, buttons, cards, or form fields anywhere in the UI
  (aircraft detail, aircraft edit form, tenant settings, dashboards);
- all Phase 39 routes return 404;
- the daily pass does no per-aircraft work (see the short-circuit rule in
  39b) — no new ledger accounts, no entries, no measurable cost;
- existing pages (aircraft edit form, expense form, flight form, tenant
  settings) are byte-for-byte unchanged for such tenants — Phase 39 adds
  **no** field to any pre-existing form except the tenant-settings field
  in 39c, which is itself gated (see there).

Every package's test list includes a "no-trace" test asserting this for a
sole-operator tenant; treat a failure there as a release blocker, not a
cosmetic issue.

Mechanics:

- The tenant's operating model lives at
  `TenantProfile.operating_model` (`OperatingModel.SHARED_OWNERSHIP`).
- Helper in `app/aircraft/routes.py` (private to the blueprint):

  ```python
  def _shared_ownership_enabled(tenant_id: int, aircraft_id: int) -> bool:
      """True when the tenant runs the shared_ownership model, OR when
      this aircraft already has AircraftOwner rows (legacy data after a
      model switch — the pages must stay reachable so an admin can view
      accounts and clear the owner set; without rows they 404, keeping
      zero trace on instances that never used the feature)."""
      profile = TenantProfile.query.filter_by(tenant_id=tenant_id).first()
      if profile and profile.operating_model == OperatingModel.SHARED_OWNERSHIP:
          return True
      return db.session.query(
          AircraftOwner.query.filter_by(aircraft_id=aircraft_id).exists()
      ).scalar()
  ```

- Management, dashboard, payment, snapshot, and statement routes return
  **404** when the helper is false. The co-owner **self-service** routes
  (39f) additionally work only for users who currently appear in
  `AircraftOwner` for that aircraft.
- Authority: managing routes are Owner/Admin only. Mirror the exact guard
  idiom of `config.renter_account` (`@login_required` + in-body
  `TenantUser` role check) — do not invent a new pattern.
- Template gating: the aircraft detail "Ownership" section and any nav
  links render only when `_shared_ownership_enabled(...)` is true for
  that aircraft (pass a flag from the route; don't query in the
  template). Using the same helper as the routes means never-users see
  nothing, while a tenant with legacy owner rows can still find the
  pages to wind them down.

---

## Delivery order

| Package | Contents | Depends on |
|---|---|---|
| 39a | `AircraftOwner` model + owners management page + aircraft-detail breakdown | — |
| 39b | `co_owner_billing.py` posting pass (buy-in, fixed shares, flight usage) + rate/billing-start config fields | 39a |
| 39c | Billing dashboard, capital balances, overdue flag (+ `co_owner_overdue_days`) | 39b |
| 39d | Payments (record, immutable, counter-entry corrections) | 39c |
| 39e | `CoOwnerValuationSnapshot` | 39c |
| 39f | Statements: admin + self-service HTML page and CSV export | 39c |
| 39g | Reserve fund — **stretch goal, explicitly skippable** (plan allows slipping to Phase 40) | 39b |
| 39h | Dev seed, user-guide section, screenshots, plan/backlog bookkeeping | all |

Commit after each package (conventional commits, e.g.
`feat(owners): add AircraftOwner model and management page (39a)`).
Propose the message; the human commits.

---

## 39a — Ownership model & management UI

### Model (`app/models.py`)

```python
class AircraftOwner(db.Model):
    """Phase 39: a co-owner of one aircraft. share_pct values for one
    aircraft always sum to exactly 100.00 (enforced in the save route —
    rows are only ever written through the manage-owners form, which
    replaces the full set atomically)."""

    __tablename__ = "aircraft_owners"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    share_pct = db.Column(db.Numeric(5, 2), nullable=False)      # 0.01 – 100.00
    buy_in_amount = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    aircraft = db.relationship("Aircraft", backref=db.backref("owners", cascade="all, delete-orphan"))
    user = db.relationship("User")

    __table_args__ = (
        db.UniqueConstraint("aircraft_id", "user_id", name="uq_aircraft_owner"),
        db.Index("ix_aircraft_owners_aircraft_id", aircraft_id),
    )
```

Two new nullable columns on `Aircraft` (same migration):

```python
# Phase 39: hourly rate charged to the flying co-owner, and the date from
# which co-owner billing considers expenses/flights. Both null until the
# owners form is first saved.
co_owner_hourly_rate = db.Column(db.Numeric(8, 2), nullable=True)
co_owner_billing_start = db.Column(db.Date, nullable=True)
```

One migration for all of the above (random 12-hex ID). Run
`scripts/check_migrations.py`.

> **Deliberately absent**: a voting-weight column. Share percentage is
> financial only — one owner, one vote (plan requirement; nothing to build,
> just don't add the column).

### Manage-owners page

Route: `GET/POST /aircraft/<int:aircraft_id>/owners` in
`app/aircraft/routes.py` (endpoint `aircraft.manage_owners`).
Guards: 404 unless `_shared_ownership_enabled`; Owner/Admin only.

One form that edits the **entire owner set atomically** (this is what makes
the sum-to-100 rule enforceable — never add per-row add/delete routes):

- One table row per owner: user `<select>` (all active tenant users),
  share % (`step="0.01" min="0.01" max="100"`), buy-in amount. Plus 3
  blank template rows for additions (server ignores rows with no user
  selected). A "remove" checkbox per existing row.
- Below the table: the two aircraft-level fields — co-owner hourly rate
  and billing-start date (`co_owner_billing_start`; when owners are saved
  for the first time and the field was left empty, default it to today).

Server-side validation (flash + re-render on failure, nothing written):

1. No duplicate users.
2. Every share > 0, ≤ 100, at most 2 decimal places.
3. `sum(share_pct) == Decimal("100.00")` **exactly** — unless zero rows
   were submitted, which is valid and means "no co-ownership on this
   aircraft" (clears all rows).
4. A single owner at 100 % is valid (sole-operator edge case from the plan).

On success: delete removed rows, update kept rows, insert new rows, save
the two aircraft fields, `db.session.commit()`, flash
`_("Ownership updated.")`. **Do not post any ledger entries here** — the
posting pass (39b) picks up buy-ins on its next run, keeping all posting
logic in one place.

### Aircraft detail breakdown

On `app/templates/aircraft/detail.html`, add an "Ownership" card (visible
only when the flag passed from the route says shared-ownership mode):
name, share %, buy-in per co-owner, ordered by share desc; a "Manage
owners" button (Owner/Admin) and a "Billing dashboard" button (39c —
render from 39c onward).

### Tests (39a) — `tests/test_shared_ownership_owners.py`

- Sum ≠ 100 rejected (60 + 30); sum == 100 accepted; single 100 % accepted.
- Zero rows clears the set.
- Duplicate user rejected; share ≤ 0 rejected; > 2 dp rejected.
- Non-shared-ownership tenant (no owner rows) → 404; non-owner role →
  403/redirect (whatever the mirrored guard does — assert that exact
  behaviour).
- Legacy-data escape hatch: tenant with owner rows but a different
  operating model → manage page still reachable, and clearing all rows
  makes it 404 afterwards.
- Tenant isolation: aircraft of another tenant → 404.
- **No-trace test**: for a sole-operator tenant, the aircraft detail page
  contains none of the Phase 39 strings ("Ownership", "Manage owners",
  "Billing dashboard") and the aircraft edit form is unchanged (no new
  fields — this phase adds none there by design).
- Breakdown card renders names/shares/buy-ins; absent for other models.
- `co_owner_billing_start` defaults to today on first save with owners.

---

## 39b — Charge computation: `app/services/co_owner_billing.py`

The heart of the phase. A single idempotent pass converts source records
into ledger entries. It is safe to run any number of times.

### Entry points

```python
def run_co_owner_billing_pass(aircraft: Aircraft) -> None:
    """Post/refresh all co-owner ledger entries for one aircraft.
    Caller owns the transaction (commit after calling)."""

def run_co_owner_billing_pass_all(today: date | None = None) -> int:
    """All aircraft that have at least one AircraftOwner row.
    Returns number of aircraft processed. Called from the daily pass."""
```

**Short-circuit rule**: `run_co_owner_billing_pass_all` starts with a
single cheap query — `SELECT DISTINCT aircraft_id FROM aircraft_owners`
— and returns 0 immediately when it is empty. On an instance that never
uses shared ownership this is the *only* work the pass ever does (no
`AircraftOwner` rows can exist there, because the manage-owners form —
the sole write path — 404s for other operating models). Do not add an
operating-model check inside the pass; the absence of owner rows is the
gate. A tenant that later switches its operating model away keeps route
access while owner rows exist (see the gating helper) so an admin can
clear the owner set — at which point the pass stops touching that
aircraft too.

Wire `run_co_owner_billing_pass_all` into the daily notification pass in
`app/services/notification_service.py`, directly alongside the existing
`materialize_recurring_expenses()` call (same error-isolation style).
Additionally call `run_co_owner_billing_pass(aircraft)` + commit at the
top of the dashboard GET route (39c) so the dashboard is always current
without waiting a day.

### Scope rules (applied identically by every sub-step)

- Only aircraft with ≥ 1 `AircraftOwner` row.
- Only source records with `date >= aircraft.co_owner_billing_start`
  (if the column is somehow NULL while owners exist, treat as "no
  billing" — post nothing).
- **The pass only ever considers current `AircraftOwner` rows.** Accounts
  of users who are no longer owners are never touched — no reversals, no
  new charges. Their history and balance simply remain (settlement of a
  departing owner is a manual payment/adjustment, out of scope).

### Accounts

Per co-owner: `BillingService.get_or_create_account(tenant_id, user_id,
BillingAccountKind.CO_OWNER, aircraft_id=aircraft.id)` — aircraft-scoped,
exactly as designed in `billing_service_design.md`.

### Sub-step 1 — buy-in (`source_type="owner_buy_in"`)

For each current owner with `buy_in_amount > 0`: expected entry =
`OPENING`, amount **`-buy_in_amount`**, `source_id=owner.id`,
`entry_date = aircraft.co_owner_billing_start`,
description `str(_("Buy-in — %(pct)s%% share", pct=...))`.

### Sub-step 2 — fixed-expense shares (`source_type="expense_share"`)

Source set: `Expense` rows for this aircraft with
`expense_category == ExpenseCategory.FIXED` and `date` in scope. Recurring
*template* rows (`recurrence` non-null) are **excluded** — only the
materialised rows they generate are billed.

Per expense, each owner's liability = `amount × share_pct / 100`,
**with the rounding residue assigned to the largest share** so the shares
always sum to the expense total exactly:

```
order owners by (share_pct ASC, user_id DESC)
for every owner except the last:  share_i = quantize(amount * pct_i / 100)
last owner (largest share):       share_n = amount - sum(previous shares)
```

Worked example — €100.01 at 33.33 / 33.33 / 33.34 %:
33.33 → 33.33, 33.33 → 33.33, largest gets 100.01 − 66.66 = **33.35**.

Each owner's entry: `CHARGE`, positive, `source_id=expense.id`,
`entry_date=expense.date`, description
`str(_("Fixed cost share (%(pct)s%%) — %(desc)s", …))` where `desc` is the
expense's type label + description.

> **No pro-rating.** Coverage-span pro-rating (`coverage_start/_end`) is a
> Phase 36 *reporting* concept for the cost dashboard. For billing, the
> full invoice amount is owed when it is dated in scope. Do not import the
> pro-rating logic.

### Sub-step 3 — flight usage (`source_type="flight_usage"`)

Skipped entirely while `aircraft.co_owner_hourly_rate` is NULL (dashboard
shows a configure-the-rate hint instead).

Source set: `Flight` rows with this `aircraft_id`,
`entry_type == LogbookEntryType.FLIGHT`, `date` in scope,
`flight_time` non-null and > 0, and **`pic_user_id` is a current
co-owner**. The PIC slot alone is charged — the second-crew slot never is
(the PIC took the aircraft; instruction-cost splitting is out of scope).
Flights whose PIC is not a co-owner (or is NULL) are never charged; the
dashboard surfaces them as "unattributed hours" (39c) so they're visible,
not silently lost.

Entry: `CHARGE`, amount `quantize(flight_time × co_owner_hourly_rate)`,
`source_id=flight.id`, `entry_date=flight.date`, description
`str(_("Flight %(date)s %(route)s — %(hours)s h", …))` with route
`"EBAW → EBKT"` when ICAO fields are set, empty otherwise.

### Idempotency + drift correction (one shared mechanism)

For each (account, `source_type`, `source_id`) the pass computes the
**expected amount** from the rules above, then compares against the
**net posted state**:

```python
def _live_entry(account_id, source_type, source_id) -> LedgerEntry | None:
    """The one entry for this source that is neither a reversal itself
    (reverses_id is NULL) nor already reversed (no other entry points at
    it via reverses_id). Returns None if the source was never posted or
    its last posting was reversed."""
```

Decision table, per (account, source):

| Live entry | Expected amount | Action |
|---|---|---|
| none | X | `BillingService.post(...)` |
| amount == X | X | nothing |
| amount ≠ X | X | `BillingService.reverse(live, note=str(_("Source record changed")))` then `post` the new amount |
| exists | source deleted / left scope / category changed / PIC changed / owner set changed the split | `reverse` only, note `str(_("Source record removed or no longer billable"))` |

"Left scope" covers every way a source stops matching its sub-step's
source-set rules (expense re-categorised to operating, flight's PIC edited
to a non-owner, date edited before billing-start, record deleted, owner's
share changed altering the split, rate change altering hours × rate, …) —
the pass doesn't special-case *why*; it just compares expected vs posted.
`created_by` on pass-posted entries: `None` (system).

All-sources enumeration for the reverse-only row: query distinct
(`account_id`, `source_type`, `source_id`) of live entries whose
`source_type` is one of the three above, for accounts of **current**
owners of this aircraft, and diff against the expected set.

### Tests (39b) — `tests/test_shared_ownership_billing_pass.py`

- Buy-in posts one negative `OPENING`; second run posts nothing (idempotent).
- Buy-in edited → reversal + repost, net = new value.
- Fixed split: known expense → per-owner amounts match `amount × pct / 100`
  to 2 dp **and sum exactly to the total** (use the 100.01 / 33-33-34 case).
- Recurring template row not billed; its materialised children are.
- Expense deleted after posting → reversal only, balance restored.
- Expense re-categorised fixed → operating → reversal.
- Flight by co-owner PIC → charge = hours × rate; flight by non-owner PIC
  → nothing; NULL rate → nothing; NULL/zero flight_time → nothing;
  FSTD entry_type → nothing.
- Operating attribution: A's flight never appears on B's account (plan test).
- Date before `co_owner_billing_start` → not billed; edit crossing the
  boundary → drift-corrected.
- Departed owner: rows for user removed from owner set are untouched on
  the next pass (no reversal), and no new charges accrue.
- Owner share change → existing expense splits drift-corrected.
- Pass runs from `run_co_owner_billing_pass_all` inside the daily pass
  (mirror how `materialize_recurring_expenses` is tested).
- **No-trace test**: on a tenant with no `AircraftOwner` rows,
  `run_co_owner_billing_pass_all` returns 0, creates no `BillingAccount`
  or `LedgerEntry` rows, and issues no per-aircraft work.
- Capital arithmetic (plan test): buy-in + payments − fixed − operating
  = `-BillingService.balance(account)`.

---

## 39c — Billing dashboard & capital accounts

### TenantProfile column (own migration)

```python
# Phase 39: days a co-owner capital balance may stay negative before the
# dashboard flags it. Editable on the tenant settings page.
co_owner_overdue_days = db.Column(db.Integer, nullable=False, default=30)
```

Expose it on the existing tenant settings form (`config` blueprint),
next to the other policy fields; integer ≥ 1. **Gated**: the field (label,
input, and its POST handling) renders and is accepted only when the
tenant's operating model is `shared_ownership` — on any other instance
the settings page must be visually identical to today, and a crafted
POST containing the field name must leave the stored value untouched.

### Dashboard route

`GET /aircraft/<int:aircraft_id>/owners/billing`
(endpoint `aircraft.owners_billing`), same guards as 39a. First action:
`run_co_owner_billing_pass(aircraft)` + commit.

Period selector `?period=<months>` — copy the semantics of
`config._renter_account_period` verbatim (default 12, invalid → 12,
`start = today − months × 30 days`).

One card/row per current co-owner showing, for the selected period:

- hours flown (sum of `flight_time` over billed `flight_usage` entries in
  the period — derive from the ledger source links, not by re-querying
  flights, so the figure always matches what was charged),
- fixed-cost liability (sum of `expense_share` charges in period),
- operating liability (sum of `flight_usage` charges in period),
- payments received in period (sum of `PAYMENT` amounts, shown positive),
- **capital balance** (all-time): `-BillingService.balance(account)`,
- overdue badge when flagged (below).

Below the cards:

- "Unattributed hours" alert listing in-scope flights whose PIC is not a
  co-owner (count + total hours + link to the airframe logbook) — only
  when non-empty.
- "Set the co-owner hourly rate to bill flight hours" hint when the rate
  is NULL.
- Payment form (39d), snapshot section (39e), statement links (39f) —
  added by their packages.

### Overdue algorithm (service helper, in `co_owner_billing.py`)

```python
def overdue_since(account: BillingAccount) -> date | None:
    """Date the capital balance last went negative (ledger balance went
    positive), or None if it is currently >= 0."""
```

Walk all entries ordered by (`entry_date`, `id`) keeping a running sum
(owes-convention). If the final sum ≤ 0 → `None`. Otherwise return the
`entry_date` of the entry after which the running sum was last ≤ 0 — i.e.
the date of the first entry of the final all-positive streak. Flagged when
`(today - overdue_since).days > tenant_profile.co_owner_overdue_days`.
This is a **visual warning only** — no enforcement, no notification type.

### Tests (39c) — `tests/test_shared_ownership_dashboard.py`

- Per-owner figures correct against a seeded scenario (2 owners, 1 fixed
  expense, flights by each, 1 payment).
- Capital balance = buy-in + payments − liabilities.
- Overdue: negative ≤ threshold days → no flag; > threshold → flag
  (freeze time or construct entry dates accordingly); balance ≥ 0 → None;
  dip-negative-then-recover-then-negative → date of the *latest* streak.
- Custom `co_owner_overdue_days` honoured; settings form round-trips it.
- **No-trace test**: on a sole-operator tenant the settings page does not
  contain the overdue-days field, and a POST smuggling the field name
  leaves the stored default untouched; dashboard route → 404.
- Unattributed-hours alert appears exactly when such flights exist.
- Rate hint shown when rate NULL; period selector filters the sums;
  dashboard GET triggers the pass (a fresh expense appears without
  waiting for the daily pass).
- 404 for wrong tenant / non-shared-ownership model; role guard.

---

## 39d — Payments & reconciliation

Mirror `config.renter_record_payment` exactly, adapted to the
aircraft-scoped account:

- `POST /aircraft/<int:aircraft_id>/owners/<int:user_id>/payment`
  (endpoint `aircraft.owner_record_payment`), Owner/Admin, 404 unless the
  target user is a current co-owner.
- Fields: amount (> 0, posted as **negative** `PAYMENT`), date
  (default today), free-text note → `description` (prefix with
  `str(_("Payment — %(note)s"))`; note optional), `created_by` = current
  user (that's the "recorded-by" audit requirement).
- **Immutability is free**: no edit/delete route exists. The dashboard's
  recent-entries list (or the statement page) shows a "reverse" button per
  payment (Owner/Admin) that calls `BillingService.reverse(entry,
  created_by, note)` — this is the plan's "counter-entry" correction.
  Reversing a reversal is already refused by the service; surface the
  `ValueError` message as a flash.

### Tests (39d) — `tests/test_shared_ownership_payments.py`

- Payment posts negative amount, adjusts capital balance immediately.
- Zero/negative input amount rejected; non-co-owner target → 404.
- Reverse nets to zero; double-reverse and reverse-of-reversal flash the
  service error; recorded-by stored.

---

## 39e — Valuation snapshots

### Model (own migration)

```python
class CoOwnerValuationSnapshot(db.Model):
    """Phase 39: immutable point-in-time capital value per co-owner.
    No update or delete route may ever exist for this table. Reproducible
    by construction: the ledger it summarises is append-only."""

    __tablename__ = "co_owner_valuation_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    valuation_date = db.Column(db.Date, nullable=False)
    share_pct = db.Column(db.Numeric(5, 2), nullable=False)       # copied at snapshot time
    capital_balance = db.Column(db.Numeric(10, 2), nullable=False)  # -balance(account, as_of)
    note = db.Column(db.String(255), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.Index("ix_covs_aircraft_id", aircraft_id),)
```

### Behaviour

- One dashboard button "Record valuation snapshot" →
  `POST /aircraft/<int:aircraft_id>/owners/valuation` (Owner/Admin) with a
  date (default today) + optional note. Runs the billing pass first, then
  writes **one row per current co-owner** with
  `capital_balance = -BillingService.balance(account, as_of=valuation_date)`.
- History table on the dashboard, grouped by valuation_date desc.
- No edit/delete routes — immutability by absence, same as the ledger.

### Tests (39e) — `tests/test_shared_ownership_valuation.py`

- Snapshot writes one row per owner with the correct as-of balance and
  copied share.
- Plan's immutability test: post further entries after snapshotting →
  stored snapshot values unchanged; a new snapshot differs.
- `as_of` respected: entries dated after the valuation date excluded.
- Guards as usual.

---

## 39f — Statements (HTML + CSV)

Reuse `BillingService.statement` / `statement_csv` unchanged — they
already produce the plan's required content: header metadata (export
date, exporter, period, holder, scope), opening balance, one itemised row
per entry (fixed shares, per-flight operating charges, payments, buy-in,
reserve contributions if 39g lands) with running balance, closing balance.
No PDF — no PDF pipeline exists in the codebase (plan explicitly allows
CSV-only in that case).

Admin side (Owner/Admin, guards as 39a), mirroring
`config.renter_account` / `renter_statement_csv`:

- `GET /aircraft/<int:aircraft_id>/owners/<int:user_id>/account` — HTML
  statement page (period selector as 39c).
- `GET /aircraft/<int:aircraft_id>/owners/<int:user_id>/account/statement.csv`
  — CSV download; the link **must** carry `hx-boost="false"` (binary
  response rule). Filename:
  `co_owner_statement_<registration>_<user_id>_<start>_<end>.csv`.

Self-service side, mirroring `reservations.my_account`:

- `GET /aircraft/<int:aircraft_id>/my-share` + `…/my-share/statement.csv`
  — any authenticated user **who is a current co-owner of this aircraft**
  (else 404); shows only their own account. Nav/aircraft-detail link
  rendered only for co-owners.

### Tests (39f) — `tests/test_shared_ownership_statements.py`

- Plan's export test: correct totals, per-entry rows, metadata present,
  opening + charges − payments = closing.
- Period filtering; CSV content-disposition + filename.
- Self-service: co-owner sees own data; another co-owner's user_id in the
  admin URL is inaccessible to them; non-co-owner → 404 (tenant-isolation
  test across tenants too).

---

## 39g — Reserve / overhaul fund (STRETCH — skip if time-boxed)

The plan marks this "may slip to Phase 40". **Implement only after
39a–39f are green and committed**; skipping it entirely is an accepted
outcome (leave its checkboxes unticked, note it in the handoff).

Design if built — deliberately minimal, no new model:

- Two nullable columns on `Aircraft` (own migration):
  `reserve_contribution_hourly` Numeric(8,2),
  `reserve_contribution_monthly` Numeric(8,2) — at most one non-null
  (validate on the owners form, where both are edited).
- New pass sub-step, `source_type="reserve_contribution"`:
  - hourly mode: piggybacks on the flight-usage source set —
    `source_id=flight.id`, charged to the PIC co-owner,
    amount = hours × hourly contribution;
  - monthly mode: one charge per owner per calendar month from
    billing-start to the current month — `source_id = year*100 + month`,
    split by share % with the same largest-share-residue rule.
  Same drift-correction table as every other sub-step.
- Fund balance on the dashboard =
  sum of all *live* `reserve_contribution` charges across the aircraft's
  co-owner accounts (a contribution is money owed *into* the fund, so it
  charges the owner and accumulates in the displayed fund figure).
  This phase only tracks the fund; spending it is out of scope.
- Tests: mode exclusivity validation; hourly and monthly amounts; monthly
  idempotency across repeated runs and month boundaries; fund total;
  drift on rate change.

---

## 39h — Seed, docs, screenshots, bookkeeping

- **Dev seed**: no shared-ownership seed exists yet. Add
  `seed_shared_ownership_tenant(...)` to `app/_seed_helpers.py` following
  the exact pattern of `seed_sole_pilot_tenant` / `seed_sole_operator_tenant`
  (a `TenantProfile` with `operating_model=OperatingModel.SHARED_OWNERSHIP`,
  `setup_complete=True`), and call it from `app/demo_seed.py` alongside the
  existing per-model tenant seeds. Content: one aircraft, 3 co-owners
  (50/30/20, buy-ins), a rate, a billing-start in the past, one fixed
  expense, flights by two of the owners, one payment, one valuation
  snapshot — enough for screenshots and manual poking.
- **User guide** (`docs/user-guide.md`): new "Shared ownership" section —
  defining owners, the two cost tiers, the capital account concept
  (capital = buy-in + payments − your fixed share − your flying),
  recording payments, snapshots, statements. Generic hostnames only.
- **Screenshots**: add manifest entries (`docs/screenshots/manifest.yml`)
  for the owners form and the billing dashboard; check whether the pages
  need query params to show seeded data before assuming a broken entry.
- **`docs/implementation_plan.md`**: tick Phase 39 boxes as packages land;
  add ✅ to the heading only when 39a–39f + 39h are done (39g may remain
  open with a note).
- **`docs/backlog.md`**: currently contains no Phase 39 items (verified
  2026-07-26) — re-check at implementation time and remove anything that
  appeared since.

---

## Out of scope (do not build)

- Voting weights, meeting/quorum features.
- Automated enforcement of overdue balances (emails, blocks) — the flag is
  visual only; no new notification type.
- Departing-owner settlement automation (manual payments/adjustments cover it).
- Charging the second-crew slot, instruction splits, per-owner rate overrides.
- Pro-rating fixed expenses for billing (reporting-only concept, Phase 36).
- PDF statements (no pipeline exists).
- Any JavaScript for this phase.
- Multi-currency (ledger core is single-currency by design).
