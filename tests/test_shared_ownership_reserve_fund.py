"""
Tests for Phase 39g (stretch goal): the co-owner reserve/overhaul fund.

See docs/implementation_plan.md, Phase 39 ("Shared Ownership").
"""

from datetime import date
from decimal import Decimal

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]

from aircraft.co_owner_form_parsing import (  # pyright: ignore[reportMissingImports]
    parse_reserve_fields,
)
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AircraftOwner,
    LedgerEntry,
    OperatingModel,
    Role,
    Tenant,
    TenantProfile,
    TenantUser,
    User,
    db,
)
import services.co_owner_billing as co_owner_billing  # pyright: ignore[reportMissingImports]


# ── Helpers ───────────────────────────────────────────────────────────────────


class _FakeForm:
    def __init__(self, data):
        self._data = data

    def get(self, name, default=""):
        return self._data.get(name, default)


def _make_tenant(app):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        db.session.add(
            TenantProfile(
                tenant_id=tenant.id,
                operating_model=OperatingModel.SHARED_OWNERSHIP,
                setup_complete=True,
            )
        )
        db.session.commit()
        return tenant.id


def _make_user(app, tenant_id, email, name, role=Role.OWNER):
    with app.app_context():
        user = User(
            email=email, password_hash=_pw_hash.hash("pw"), is_active=True, name=name
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant_id, role=role))
        db.session.commit()
        return user.id


def _make_aircraft(
    app, tenant_id, registration="OO-SHR", billing_start=date(2026, 1, 1)
):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id,
            registration=registration,
            make="Cessna",
            model="172S",
            co_owner_billing_start=billing_start,
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_owner(app, aircraft_id, user_id, share_pct=100, buy_in=0):
    with app.app_context():
        o = AircraftOwner(
            aircraft_id=aircraft_id,
            user_id=user_id,
            share_pct=Decimal(str(share_pct)),
            buy_in_amount=Decimal(str(buy_in)),
        )
        db.session.add(o)
        db.session.commit()
        return o.id


def _add_flight(app, aircraft_id, pic_user_id, flight_date, flight_time):
    from models import Flight, LogbookEntryType

    with app.app_context():
        f = Flight(
            aircraft_id=aircraft_id,
            date=flight_date,
            departure_icao="EBOS",
            arrival_icao="EBBR",
            pic_user_id=pic_user_id,
            flight_time=flight_time,
            entry_type=LogbookEntryType.FLIGHT,
        )
        db.session.add(f)
        db.session.commit()
        return f.id


def _run_pass(app, aircraft_id):
    with app.app_context():
        ac = db.session.get(Aircraft, aircraft_id)
        co_owner_billing.run_co_owner_billing_pass(ac)
        db.session.commit()


def _live_reserve_entries(app, tenant_id, user_id, aircraft_id):
    from models import BillingAccount, BillingAccountKind

    with app.app_context():
        acc = BillingAccount.query.filter_by(
            tenant_id=tenant_id,
            user_id=user_id,
            kind=BillingAccountKind.CO_OWNER,
            aircraft_id=aircraft_id,
        ).first()
        if acc is None:
            return []
        entries = (
            LedgerEntry.query.filter_by(
                account_id=acc.id, source_type="reserve_contribution"
            )
            .filter(LedgerEntry.reverses_id.is_(None))
            .all()
        )
        return [
            e
            for e in entries
            if LedgerEntry.query.filter_by(reverses_id=e.id).first() is None
        ]


# ── Form validation ────────────────────────────────────────────────────────────


class TestParseReserveFields:
    def test_neither_set_is_valid(self):
        hourly, monthly, errors = parse_reserve_fields(_FakeForm({}))
        assert hourly is None
        assert monthly is None
        assert errors == []

    def test_only_hourly_set(self):
        hourly, monthly, errors = parse_reserve_fields(
            _FakeForm({"reserve_contribution_hourly": "15.00"})
        )
        assert hourly == Decimal("15.00")
        assert monthly is None
        assert errors == []

    def test_only_monthly_set(self):
        hourly, monthly, errors = parse_reserve_fields(
            _FakeForm({"reserve_contribution_monthly": "50.00"})
        )
        assert hourly is None
        assert monthly == Decimal("50.00")
        assert errors == []

    def test_both_set_is_rejected(self):
        hourly, monthly, errors = parse_reserve_fields(
            _FakeForm(
                {
                    "reserve_contribution_hourly": "15.00",
                    "reserve_contribution_monthly": "50.00",
                }
            )
        )
        assert any("not both" in e for e in errors)

    def test_negative_hourly_rejected(self):
        hourly, _monthly, errors = parse_reserve_fields(
            _FakeForm({"reserve_contribution_hourly": "-5"})
        )
        assert hourly is None
        assert errors != []

    def test_non_numeric_monthly_rejected(self):
        _hourly, monthly, errors = parse_reserve_fields(
            _FakeForm({"reserve_contribution_monthly": "abc"})
        )
        assert monthly is None
        assert errors != []

    def test_infinite_hourly_rejected(self):
        hourly, _monthly, errors = parse_reserve_fields(
            _FakeForm({"reserve_contribution_hourly": "inf"})
        )
        assert hourly is None
        assert errors != []


# ── Route: manage_owners saves reserve fields ────────────────────────────────


class TestManageOwnersReserveFields:
    def test_saves_hourly_only(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        with app.app_context():
            uid_ = User.query.filter_by(email="alice@example.com").first().id
        with client.session_transaction() as sess:
            sess["user_id"] = uid_
        r = client.post(
            f"/aircraft/{acid}/owners",
            data={
                "owner_user_id[]": [str(uid)],
                "owner_share_pct[]": ["100"],
                "reserve_contribution_hourly": "15.00",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            assert ac.reserve_contribution_hourly == Decimal("15.00")
            assert ac.reserve_contribution_monthly is None

    def test_both_set_rejected_and_not_saved(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        r = client.post(
            f"/aircraft/{acid}/owners",
            data={
                "owner_user_id[]": [str(uid)],
                "owner_share_pct[]": ["100"],
                "reserve_contribution_hourly": "15.00",
                "reserve_contribution_monthly": "50.00",
            },
        )
        assert r.status_code == 200
        assert b"not both" in r.data
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            assert ac.reserve_contribution_hourly is None
            assert ac.reserve_contribution_monthly is None


# ── Hourly mode ────────────────────────────────────────────────────────────────


class TestHourlyMode:
    def test_charge_equals_hours_times_rate(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.reserve_contribution_hourly = Decimal("10.00")
            db.session.commit()
        _add_flight(app, acid, uid, date(2026, 2, 1), Decimal("2.0"))

        _run_pass(app, acid)

        entries = _live_reserve_entries(app, tid, uid, acid)
        assert len(entries) == 1
        assert entries[0].amount == Decimal("20.00")

    def test_flight_by_non_owner_is_skipped(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        outsider = _make_user(app, tid, "dave@example.com", "Dave")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.reserve_contribution_hourly = Decimal("10.00")
            db.session.commit()
        _add_flight(app, acid, outsider, date(2026, 2, 1), Decimal("2.0"))

        _run_pass(app, acid)

        assert _live_reserve_entries(app, tid, uid, acid) == []

    def test_idempotent_on_second_run(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.reserve_contribution_hourly = Decimal("10.00")
            db.session.commit()
        _add_flight(app, acid, uid, date(2026, 2, 1), Decimal("2.0"))

        _run_pass(app, acid)
        _run_pass(app, acid)

        entries = _live_reserve_entries(app, tid, uid, acid)
        assert len(entries) == 1

    def test_drift_on_rate_change(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.reserve_contribution_hourly = Decimal("10.00")
            db.session.commit()
        _add_flight(app, acid, uid, date(2026, 2, 1), Decimal("2.0"))
        _run_pass(app, acid)

        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.reserve_contribution_hourly = Decimal("20.00")
            db.session.commit()
        _run_pass(app, acid)

        entries = _live_reserve_entries(app, tid, uid, acid)
        assert len(entries) == 1
        assert entries[0].amount == Decimal("40.00")

    def test_rate_cleared_reverses_all_contributions(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.reserve_contribution_hourly = Decimal("10.00")
            db.session.commit()
        _add_flight(app, acid, uid, date(2026, 2, 1), Decimal("2.0"))
        _run_pass(app, acid)
        assert _live_reserve_entries(app, tid, uid, acid) != []

        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.reserve_contribution_hourly = None
            db.session.commit()
        _run_pass(app, acid)

        assert _live_reserve_entries(app, tid, uid, acid) == []


# ── Monthly mode ───────────────────────────────────────────────────────────────


class TestMonthlyMode:
    def test_one_charge_per_owner_per_month_split_by_share(self, app):
        tid = _make_tenant(app)
        alice = _make_user(app, tid, "alice@example.com", "Alice")
        bob = _make_user(app, tid, "bob@example.com", "Bob")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, alice, 60)
        _add_owner(app, acid, bob, 40)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.reserve_contribution_monthly = Decimal("100.00")
            db.session.commit()

        _run_pass(app, acid)

        alice_entries = _live_reserve_entries(app, tid, alice, acid)
        bob_entries = _live_reserve_entries(app, tid, bob, acid)
        # From billing_start (Jan 2026) to "today" (test env clock) inclusive
        assert len(alice_entries) == len(bob_entries)
        assert len(alice_entries) >= 1
        for a_entry, b_entry in zip(
            sorted(alice_entries, key=lambda e: e.entry_date),
            sorted(bob_entries, key=lambda e: e.entry_date),
        ):
            assert a_entry.amount + b_entry.amount == Decimal("100.00")
            assert a_entry.amount == Decimal("60.00")
            assert b_entry.amount == Decimal("40.00")

    def test_idempotent_on_second_run(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.reserve_contribution_monthly = Decimal("50.00")
            db.session.commit()

        _run_pass(app, acid)
        with app.app_context():
            first_count = len(_live_reserve_entries(app, tid, uid, acid))
        _run_pass(app, acid)
        with app.app_context():
            second_count = len(_live_reserve_entries(app, tid, uid, acid))

        assert first_count == second_count

    def test_new_month_appears_on_later_run(self, app, monkeypatch):
        """Crossing a month boundary between two pass runs posts exactly
        one additional monthly charge, without duplicating prior months."""

        class _FrozenDate(date):
            _frozen = date(2026, 1, 15)

            @classmethod
            def today(cls):
                return cls._frozen

        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.reserve_contribution_monthly = Decimal("50.00")
            db.session.commit()

        monkeypatch.setattr(co_owner_billing, "date", _FrozenDate)
        _run_pass(app, acid)
        with app.app_context():
            count_jan = len(_live_reserve_entries(app, tid, uid, acid))
        assert count_jan == 1

        _FrozenDate._frozen = date(2026, 2, 10)
        _run_pass(app, acid)
        with app.app_context():
            count_feb = len(_live_reserve_entries(app, tid, uid, acid))
        assert count_feb == 2

    def test_year_boundary_rollover(self, app, monkeypatch):
        """billing_start in November, "today" frozen to the following
        January: the month-enumeration loop must cross Dec -> Jan (year
        increment), producing exactly 3 monthly charges (Nov, Dec, Jan)."""

        class _FrozenDate(date):
            @classmethod
            def today(cls):
                return date(2027, 1, 5)

        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 11, 1))
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.reserve_contribution_monthly = Decimal("50.00")
            db.session.commit()

        monkeypatch.setattr(co_owner_billing, "date", _FrozenDate)
        _run_pass(app, acid)

        entries = _live_reserve_entries(app, tid, uid, acid)
        months = sorted((e.entry_date.year, e.entry_date.month) for e in entries)
        assert months == [(2026, 11), (2026, 12), (2027, 1)]

    def test_drift_on_rate_change(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.reserve_contribution_monthly = Decimal("50.00")
            db.session.commit()
        _run_pass(app, acid)

        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.reserve_contribution_monthly = Decimal("75.00")
            db.session.commit()
        _run_pass(app, acid)

        entries = _live_reserve_entries(app, tid, uid, acid)
        assert all(e.amount == Decimal("75.00") for e in entries)


# ── Fund total ─────────────────────────────────────────────────────────────────


class TestReserveFundBalance:
    def test_sums_live_contributions_across_owners(self, app):
        tid = _make_tenant(app)
        alice = _make_user(app, tid, "alice@example.com", "Alice")
        bob = _make_user(app, tid, "bob@example.com", "Bob")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, alice, 60)
        _add_owner(app, acid, bob, 40)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.reserve_contribution_hourly = Decimal("10.00")
            db.session.commit()
        _add_flight(app, acid, alice, date(2026, 2, 1), Decimal("2.0"))
        _add_flight(app, acid, bob, date(2026, 2, 2), Decimal("1.0"))

        _run_pass(app, acid)

        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            balance = co_owner_billing.reserve_fund_balance(ac)
        assert balance == Decimal("30.00")

    def test_zero_when_no_contributions(self, app):
        tid = _make_tenant(app)
        _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            balance = co_owner_billing.reserve_fund_balance(ac)
        assert balance == Decimal("0.00")

    def test_dashboard_shows_fund_balance(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.reserve_contribution_hourly = Decimal("10.00")
            db.session.commit()
        _add_flight(app, acid, uid, date(2026, 2, 1), Decimal("2.0"))
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert r.status_code == 200
        assert b"Reserve" in r.data
        assert b"20.00" in r.data

    def test_dashboard_omits_fund_when_not_configured(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert r.status_code == 200
        assert b"Reserve / overhaul fund balance" not in r.data
