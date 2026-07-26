"""
Tests for Phase 39c: the co-owner billing dashboard, capital balances, and
the overdue flag (+ TenantProfile.co_owner_overdue_days).

See docs/phase39_shared_ownership_spec.md ("39c — Billing dashboard &
capital accounts").
"""

from datetime import date
from decimal import Decimal

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AircraftOwner,
    BillingAccount,
    BillingAccountKind,
    LedgerEntryType,
    OperatingModel,
    Role,
    Tenant,
    TenantProfile,
    TenantUser,
    User,
    db,
)
from services.billing import BillingService  # pyright: ignore[reportMissingImports]
from services.co_owner_billing import (  # pyright: ignore[reportMissingImports]
    overdue_since,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_tenant(app, operating_model=OperatingModel.SHARED_OWNERSHIP):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        db.session.add(
            TenantProfile(
                tenant_id=tenant.id,
                operating_model=operating_model,
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
    app, tenant_id, registration="OO-SHR", hourly_rate=None, billing_start=None
):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id,
            registration=registration,
            make="Cessna",
            model="172S",
            co_owner_hourly_rate=hourly_rate,
            co_owner_billing_start=billing_start,
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_owner(app, aircraft_id, user_id, share_pct, buy_in=0):
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


def _login(app, client, email):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _account(tenant_id, user_id, aircraft_id):
    return BillingAccount.query.filter_by(
        tenant_id=tenant_id,
        user_id=user_id,
        kind=BillingAccountKind.CO_OWNER,
        aircraft_id=aircraft_id,
    ).first()


# ── Dashboard route ────────────────────────────────────────────────────────────


class TestOwnersBillingRoute:
    def test_404_for_non_shared_ownership_tenant(self, app, client):
        tid = _make_tenant(app, operating_model=OperatingModel.SOLE_OPERATOR)
        _make_user(app, tid, "owner@example.com", "Owner")
        acid = _make_aircraft(app, tid)
        _login(app, client, "owner@example.com")
        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert r.status_code == 404

    def test_tenant_isolation_404(self, app, client):
        tid1 = _make_tenant(app)
        _make_user(app, tid1, "owner1@example.com", "Owner1")
        tid2 = _make_tenant(app)
        acid2 = _make_aircraft(app, tid2, "OO-OTH")
        _login(app, client, "owner1@example.com")
        r = client.get(f"/aircraft/{acid2}/owners/billing")
        assert r.status_code == 404

    def test_403_for_non_owner_role(self, app, client):
        tid = _make_tenant(app)
        _make_user(app, tid, "owner@example.com", "Owner")
        pilot_uid = _make_user(app, tid, "pilot@example.com", "Pilot", role=Role.PILOT)
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, pilot_uid, 100)
        with client.session_transaction() as sess:
            sess["user_id"] = pilot_uid
        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert r.status_code == 403

    def test_dashboard_shows_per_owner_figures(self, app, client):
        tid = _make_tenant(app)
        alice = _make_user(app, tid, "alice@example.com", "Alice")
        bob = _make_user(app, tid, "bob@example.com", "Bob")
        acid = _make_aircraft(
            app, tid, hourly_rate="100.00", billing_start=date(2026, 1, 1)
        )
        _add_owner(app, acid, alice, 50, buy_in=1000)
        _add_owner(app, acid, bob, 50, buy_in=1000)

        with app.app_context():
            from models import Expense, ExpenseCategory, Flight, LogbookEntryType

            db.session.add(
                Expense(
                    aircraft_id=acid,
                    date=date(2026, 2, 1),
                    expense_type="insurance",
                    expense_category=ExpenseCategory.FIXED,
                    amount=Decimal("200"),
                )
            )
            db.session.add(
                Flight(
                    aircraft_id=acid,
                    date=date(2026, 2, 2),
                    departure_icao="EBOS",
                    arrival_icao="EBBR",
                    pic_user_id=alice,
                    flight_time=Decimal("2.0"),
                    entry_type=LogbookEntryType.FLIGHT,
                )
            )
            db.session.commit()

        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert r.status_code == 200
        assert b"Alice" in r.data
        assert b"Bob" in r.data
        # Alice: buy-in 1000 - fixed share 100 - flight 200 = capital 700
        assert b"700.00" in r.data
        # Bob: buy-in 1000 - fixed share 100 = capital 900
        assert b"900.00" in r.data

    def test_dashboard_get_triggers_billing_pass(self, app, client):
        """A fresh expense appears in the dashboard's figures immediately,
        without waiting for the daily pass."""
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)

        with app.app_context():
            from models import Expense, ExpenseCategory

            db.session.add(
                Expense(
                    aircraft_id=acid,
                    date=date(2026, 2, 1),
                    expense_type="insurance",
                    expense_category=ExpenseCategory.FIXED,
                    amount=Decimal("50"),
                )
            )
            db.session.commit()

        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert r.status_code == 200
        with app.app_context():
            acc = _account(tid, uid, acid)
            assert BillingService.balance(acc) == Decimal("50.00")

    def test_period_selector_filters_sums(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(
            app, tid, hourly_rate="100.00", billing_start=date(2020, 1, 1)
        )
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            from models import Flight, LogbookEntryType

            db.session.add(
                Flight(
                    aircraft_id=acid,
                    date=date(2020, 6, 1),
                    departure_icao="EBOS",
                    arrival_icao="EBBR",
                    pic_user_id=uid,
                    flight_time=Decimal("3.0"),
                    entry_type=LogbookEntryType.FLIGHT,
                )
            )
            db.session.commit()

        _login(app, client, "alice@example.com")
        # 1-month period excludes the old flight
        r = client.get(f"/aircraft/{acid}/owners/billing?period=1")
        assert r.status_code == 200
        assert b"0.0 h" in r.data

    def test_invalid_period_falls_back_to_default(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}/owners/billing?period=not-a-number")
        assert r.status_code == 200

    def test_zero_or_negative_period_falls_back_to_default(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}/owners/billing?period=0")
        assert r.status_code == 200
        r2 = client.get(f"/aircraft/{acid}/owners/billing?period=-5")
        assert r2.status_code == 200

    def test_payments_received_in_period_shown_positive(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            acc = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.CO_OWNER, aircraft_id=acid
            )
            BillingService.post(
                acc,
                LedgerEntryType.PAYMENT,
                Decimal("-250"),
                "Payment",
                date(2026, 2, 1),
                source_type="payment",
            )
            db.session.commit()

        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert r.status_code == 200
        assert b"250.00" in r.data

    def test_rate_hint_shown_when_rate_null(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(
            app, tid, hourly_rate=None, billing_start=date(2026, 1, 1)
        )
        _add_owner(app, acid, uid, 100)
        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert b"bill flight hours" in r.data

    def test_rate_hint_absent_when_rate_set(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(
            app, tid, hourly_rate="100.00", billing_start=date(2026, 1, 1)
        )
        _add_owner(app, acid, uid, 100)
        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert b"bill flight hours" not in r.data

    def test_unattributed_hours_alert_appears_when_flights_exist(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        other = _make_user(app, tid, "dave@example.com", "Dave", role=Role.PILOT)
        acid = _make_aircraft(
            app, tid, hourly_rate="100.00", billing_start=date(2026, 1, 1)
        )
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            from models import Flight, LogbookEntryType

            db.session.add(
                Flight(
                    aircraft_id=acid,
                    date=date(2026, 2, 1),
                    departure_icao="EBOS",
                    arrival_icao="EBBR",
                    pic_user_id=other,
                    flight_time=Decimal("1.5"),
                    entry_type=LogbookEntryType.FLIGHT,
                )
            )
            db.session.commit()

        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert b"not billed to anyone" in r.data

    def test_unattributed_hours_alert_absent_when_no_such_flights(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(
            app, tid, hourly_rate="100.00", billing_start=date(2026, 1, 1)
        )
        _add_owner(app, acid, uid, 100)
        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert b"not billed to anyone" not in r.data

    def test_overdue_badge_shown_when_flagged(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2020, 1, 1))
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            from services.co_owner_billing import run_co_owner_billing_pass

            ac = db.session.get(Aircraft, acid)
            run_co_owner_billing_pass(ac)
            acc = _account(tid, uid, acid)
            BillingService.post(
                acc,
                LedgerEntryType.CHARGE,
                Decimal("500"),
                "Old charge",
                date(2020, 1, 2),
                source_type="test",
                source_id=1,
            )
            db.session.commit()

        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert b"Overdue" in r.data


# ── overdue_since ─────────────────────────────────────────────────────────────


class TestOverdueSince:
    def test_none_when_balance_non_negative(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100, buy_in=100)
        with app.app_context():
            from services.co_owner_billing import run_co_owner_billing_pass

            ac = db.session.get(Aircraft, acid)
            run_co_owner_billing_pass(ac)
            db.session.commit()
            acc = _account(tid, uid, acid)
            assert overdue_since(acc) is None

    def test_returns_streak_start_when_negative(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            acc = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.CO_OWNER, aircraft_id=acid
            )
            BillingService.post(
                acc,
                LedgerEntryType.CHARGE,
                Decimal("100"),
                "Charge",
                date(2026, 1, 5),
                source_type="test",
                source_id=1,
            )
            db.session.commit()
            assert overdue_since(acc) == date(2026, 1, 5)

    def test_latest_streak_after_dip_recover_dip(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            acc = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.CO_OWNER, aircraft_id=acid
            )
            # Go negative on Jan 5
            BillingService.post(
                acc,
                LedgerEntryType.CHARGE,
                Decimal("100"),
                "Charge 1",
                date(2026, 1, 5),
                source_type="test",
                source_id=1,
            )
            db.session.commit()
            # Recover on Jan 10 (payment brings balance back to 0)
            BillingService.post(
                acc,
                LedgerEntryType.PAYMENT,
                Decimal("-100"),
                "Payment",
                date(2026, 1, 10),
                source_type="payment",
            )
            db.session.commit()
            # Go negative again on Jan 20 — this is the "latest streak"
            BillingService.post(
                acc,
                LedgerEntryType.CHARGE,
                Decimal("50"),
                "Charge 2",
                date(2026, 1, 20),
                source_type="test",
                source_id=2,
            )
            db.session.commit()
            assert overdue_since(acc) == date(2026, 1, 20)

    def test_overdue_flag_respects_threshold(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            acc = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.CO_OWNER, aircraft_id=acid
            )
            BillingService.post(
                acc,
                LedgerEntryType.CHARGE,
                Decimal("100"),
                "Charge",
                date.today() - date.resolution * 10,
                source_type="test",
                source_id=1,
            )
            profile = TenantProfile.query.filter_by(tenant_id=tid).first()
            profile.co_owner_overdue_days = 30
            db.session.commit()

        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert b"Overdue" not in r.data


# ── Tenant settings: co_owner_overdue_days ───────────────────────────────────


class TestOverdueDaysSetting:
    def test_saves_valid_value_on_shared_ownership_tenant(self, app, client):
        tid = _make_tenant(app)
        _make_user(app, tid, "owner@example.com", "Owner")
        _login(app, client, "owner@example.com")
        r = client.post(
            "/config/profile",
            data={
                "operating_model": "shared_ownership",
                "co_owner_overdue_days": "45",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            profile = TenantProfile.query.filter_by(tenant_id=tid).first()
            assert profile.co_owner_overdue_days == 45

    def test_non_numeric_value_ignored(self, app, client):
        tid = _make_tenant(app)
        _make_user(app, tid, "owner@example.com", "Owner")
        _login(app, client, "owner@example.com")
        r = client.post(
            "/config/profile",
            data={
                "operating_model": "shared_ownership",
                "co_owner_overdue_days": "not-a-number",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            profile = TenantProfile.query.filter_by(tenant_id=tid).first()
            assert profile.co_owner_overdue_days == 30

    def test_ignored_on_non_shared_ownership_tenant(self, app, client):
        tid = _make_tenant(app, operating_model=OperatingModel.SOLE_OPERATOR)
        _make_user(app, tid, "owner@example.com", "Owner")
        _login(app, client, "owner@example.com")
        r = client.post(
            "/config/profile",
            data={
                "operating_model": "sole_operator",
                "co_owner_overdue_days": "999",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            profile = TenantProfile.query.filter_by(tenant_id=tid).first()
            assert profile.co_owner_overdue_days == 30

    def test_settings_page_no_trace_for_sole_operator(self, app, client):
        tid = _make_tenant(app, operating_model=OperatingModel.SOLE_OPERATOR)
        _make_user(app, tid, "owner@example.com", "Owner")
        _login(app, client, "owner@example.com")
        r = client.get("/config/")
        assert b"co_owner_overdue_days" not in r.data
        assert b"Co-owner overdue threshold" not in r.data

    def test_settings_page_shows_field_for_shared_ownership(self, app, client):
        tid = _make_tenant(app)
        _make_user(app, tid, "owner@example.com", "Owner")
        _login(app, client, "owner@example.com")
        r = client.get("/config/")
        assert b"co_owner_overdue_days" in r.data
