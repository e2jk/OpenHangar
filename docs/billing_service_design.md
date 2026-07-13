# Billing Service — Shared Design (Phases 37 / 38 / 39)

Three planned phases need to charge people money and track what they owe:

| Phase | Who is billed | For what |
|---|---|---|
| 37 — Rental Operations | Renters | Hours flown at the aircraft's rate, minus fuel credits |
| 38 — Shared Ownership | Co-owners | Fixed costs split by share %, operating costs by usage |
| 39 — Flying Club | Members | Hours at member-type rates, plus membership dues |

All three reduce to the same primitives: **an account per person, an
append-only ledger of charges and payments, a derived balance, and a
period statement**. This document fixes that shared core once, so whichever
phase is implemented first builds it and the other two reuse it unchanged.

Anything *not* shared (share-percentage validation, membership types,
dispatch records, rate tables) stays in its owning phase and merely *posts
entries* to this core.

---

## Data model

Two tables, in `app/models.py`, with an Alembic migration
(ID via `secrets.token_hex(6)` as always).

### `BillingAccount`

One row per (tenant, user, scope). The scope discriminates the three use
cases without subclassing:

```python
class BillingAccountKind:
    RENTER = "renter"        # Phase 37 — scoped to the tenant (all aircraft)
    CO_OWNER = "co_owner"    # Phase 38 — scoped to one aircraft
    MEMBER = "member"        # Phase 39 — scoped to the tenant


class BillingAccount(db.Model):
    __tablename__ = "billing_accounts"

    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id       = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    aircraft_id   = db.Column(db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=True)  # co_owner only
    kind          = db.Column(db.String(16), nullable=False)  # BillingAccountKind
    currency      = db.Column(db.String(4), nullable=False, default="EUR")
    created_at    = db.Column(db.DateTime(timezone=True), nullable=False, default=...)

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "user_id", "kind", "aircraft_id",
                            name="uq_billing_account_scope"),
        db.Index("ix_billing_accounts_tenant_id", tenant_id),
    )
```

Accounts are created lazily by the service the first time an entry is posted
for a (user, kind, scope) combination — no UI to "create an account".

### `LedgerEntry`

Append-only. **No update or delete route may ever exist for this table.**
Corrections are made by posting a reversal entry (same amount, opposite
sign, `reverses_id` set) followed by a corrected entry if needed. This is
the invariant that makes statements reproducible; it is also why Phase 38's
"valuation snapshots" need no extra immutability machinery — history cannot
change under them.

```python
class LedgerEntryType:
    CHARGE     = "charge"      # money the account holder owes (positive amount)
    PAYMENT    = "payment"     # money received from the holder (negative amount)
    CREDIT     = "credit"      # reduction of debt, e.g. fuel reimbursement (negative)
    ADJUSTMENT = "adjustment"  # manual correction, either sign, requires note
    OPENING    = "opening"     # opening balance / co-owner buy-in


class LedgerEntry(db.Model):
    __tablename__ = "ledger_entries"

    id            = db.Column(db.Integer, primary_key=True)
    account_id    = db.Column(db.Integer, db.ForeignKey("billing_accounts.id", ondelete="CASCADE"), nullable=False)
    entry_type    = db.Column(db.String(16), nullable=False)   # LedgerEntryType
    amount        = db.Column(db.Numeric(10, 2), nullable=False)  # signed; see convention below
    description   = db.Column(db.String(255), nullable=False)
    entry_date    = db.Column(db.Date, nullable=False)          # the business date, not created_at
    # Link back to the domain object that produced the entry, for drill-down:
    source_type   = db.Column(db.String(32), nullable=True)     # e.g. "rental_charge", "expense_share"
    source_id     = db.Column(db.Integer, nullable=True)
    reverses_id   = db.Column(db.Integer, db.ForeignKey("ledger_entries.id", ondelete="RESTRICT"), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at    = db.Column(db.DateTime(timezone=True), nullable=False, default=...)

    __table_args__ = (db.Index("ix_ledger_entries_account_id", account_id),)
```

**Sign convention:** positive = the holder owes more; negative = the holder
owes less. `balance = sum(amount)`; a **positive balance means the holder
owes money**. (Phase 38's capital accounts read the same numbers inverted
for display — "capital remaining" = buy-in minus what has been consumed —
that is a presentation concern, not a schema one.)

---

## Service API

`app/services/billing.py` — all writes go through the service; routes never
insert `LedgerEntry` rows directly.

```python
class BillingService:
    @staticmethod
    def get_or_create_account(tenant_id, user_id, kind, aircraft_id=None) -> BillingAccount: ...

    @staticmethod
    def post(account, entry_type, amount, description, entry_date,
             source_type=None, source_id=None, created_by=None) -> LedgerEntry:
        """Validates sign against entry_type; commits nothing (caller owns the txn)."""

    @staticmethod
    def reverse(entry, created_by, note) -> LedgerEntry:
        """Posts the mirror entry with reverses_id set. Refuses to reverse a reversal
        and refuses to reverse the same entry twice."""

    @staticmethod
    def balance(account, as_of: date | None = None) -> Decimal: ...

    @staticmethod
    def statement(account, start: date, end: date) -> Statement:
        """Opening balance (sum of entries before start), chronological entries
        in [start, end], closing balance. Plain dataclass, used by both the
        HTML view and the CSV export."""

    @staticmethod
    def statement_csv(statement) -> str:
        """Header rows: export date, exporter, period, account holder, scope.
        Then one row per entry: date, type, description, amount, running balance."""
```

Validation rules enforced in `post()`:

- `CHARGE` must be > 0; `PAYMENT` and `CREDIT` must be < 0; `OPENING` and
  `ADJUSTMENT` may be either sign but `ADJUSTMENT` requires a non-empty
  description.
- Account currency must match — no multi-currency accounts. The tenant is
  assumed single-currency (same assumption `Expense` already makes; EUR
  default).
- `Decimal` end to end; never float. Round half-up to 2 decimals at posting
  time (`quantize(Decimal("0.01"), ROUND_HALF_UP)`).

## What each phase builds on top

- **Phase 37**: `RentalCharge` (draft → finalized) posts one `CHARGE` and
  zero or more `CREDIT` entries on finalization (`source_type="rental_charge"`).
  Payments recorded manually post `PAYMENT` entries. See
  [`phase37_rental_spec.md`](phase37_rental_spec.md).
- **Phase 38**: co-owner buy-in posts `OPENING`; each fixed `Expense` posts
  one `CHARGE` per co-owner (`amount × share_pct`, `source_type="expense_share"`);
  flying hours post usage charges. `CoOwnerValuationSnapshot` just records
  `balance(account, as_of)` — immutability comes free from the ledger.
- **Phase 39**: membership dues post scheduled `CHARGE` entries; flights
  post hour charges at the member-type rate.

## Permissions

- Account holders may **view** their own account, statement, and CSV.
- `is_owner` (Owner/Admin) may view all accounts in the tenant, post
  payments/adjustments, and trigger reversals.
- Nobody may edit or delete entries — there are no such routes.

## Testing the core (built with whichever phase lands first)

- Sign validation per entry type; adjustment-requires-note.
- Balance: mixed entries sum correctly; `as_of` excludes later entries.
- Reversal: net zero; double-reversal and reversal-of-reversal refused.
- Statement: opening + entries − … = closing; entries outside the period
  excluded; CSV totals match; header metadata present.
- Lazy account creation is idempotent (unique constraint holds under the
  "two requests race" test).
- Tenant isolation: user A cannot fetch user B's account or statement;
  accounts of tenant 1 invisible to tenant 2's owner.
