"""
Tests for Phase 39d: co-owner payments & reconciliation.

See docs/phase39_shared_ownership_spec.md ("39d — Payments & reconciliation").
"""

from datetime import date
from decimal import Decimal

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AircraftOwner,
    BillingAccount,
    BillingAccountKind,
    LedgerEntry,
    OperatingModel,
    Role,
    Tenant,
    TenantProfile,
    TenantUser,
    User,
    db,
)
from services.billing import BillingService  # pyright: ignore[reportMissingImports]


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


def _balance(tenant_id, user_id, aircraft_id):
    acc = _account(tenant_id, user_id, aircraft_id)
    if acc is None:
        return Decimal("0.00")
    return BillingService.balance(acc)


# ── Record payment ─────────────────────────────────────────────────────────────


class TestRecordPayment:
    def test_posts_negative_amount_and_adjusts_balance(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        _login(app, client, "alice@example.com")

        r = client.post(
            f"/aircraft/{acid}/owners/{uid}/payment",
            data={"amount": "250.00", "date": "2026-03-01"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            assert _balance(tid, uid, acid) == Decimal("-250.00")

    def test_zero_amount_rejected(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        _login(app, client, "alice@example.com")

        client.post(
            f"/aircraft/{acid}/owners/{uid}/payment",
            data={"amount": "0"},
        )
        with app.app_context():
            assert _balance(tid, uid, acid) == Decimal("0.00")

    def test_negative_amount_rejected(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        _login(app, client, "alice@example.com")

        client.post(
            f"/aircraft/{acid}/owners/{uid}/payment",
            data={"amount": "-50"},
        )
        with app.app_context():
            assert _balance(tid, uid, acid) == Decimal("0.00")

    def test_non_numeric_amount_rejected(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        _login(app, client, "alice@example.com")

        r = client.post(
            f"/aircraft/{acid}/owners/{uid}/payment",
            data={"amount": "not-a-number"},
        )
        assert r.status_code == 302
        with app.app_context():
            assert _balance(tid, uid, acid) == Decimal("0.00")

    def test_invalid_date_rejects_the_payment(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        _login(app, client, "alice@example.com")

        r = client.post(
            f"/aircraft/{acid}/owners/{uid}/payment",
            data={"amount": "100", "date": "garbage"},
        )
        assert r.status_code == 302
        with app.app_context():
            assert _balance(tid, uid, acid) == Decimal("0.00")

    def test_missing_date_defaults_to_today(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        _login(app, client, "alice@example.com")

        r = client.post(
            f"/aircraft/{acid}/owners/{uid}/payment",
            data={"amount": "100"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            acc = _account(tid, uid, acid)
            entry = LedgerEntry.query.filter_by(account_id=acc.id).first()
            assert entry.entry_date == date.today()

    def test_note_included_in_description(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        _login(app, client, "alice@example.com")

        client.post(
            f"/aircraft/{acid}/owners/{uid}/payment",
            data={"amount": "100", "note": "Bank transfer ref 123"},
        )
        with app.app_context():
            acc = _account(tid, uid, acid)
            entry = LedgerEntry.query.filter_by(account_id=acc.id).first()
            assert "Bank transfer ref 123" in entry.description

    def test_no_note_uses_default_description(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        _login(app, client, "alice@example.com")

        client.post(f"/aircraft/{acid}/owners/{uid}/payment", data={"amount": "100"})
        with app.app_context():
            acc = _account(tid, uid, acid)
            entry = LedgerEntry.query.filter_by(account_id=acc.id).first()
            assert entry.description

    def test_recorder_stored_as_created_by(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        alice_uid = _login(app, client, "alice@example.com")

        client.post(f"/aircraft/{acid}/owners/{uid}/payment", data={"amount": "100"})
        with app.app_context():
            acc = _account(tid, uid, acid)
            entry = LedgerEntry.query.filter_by(account_id=acc.id).first()
            assert entry.created_by_id == alice_uid

    def test_non_co_owner_target_404(self, app, client):
        tid = _make_tenant(app)
        alice = _make_user(app, tid, "alice@example.com", "Alice")
        outsider = _make_user(app, tid, "eve@example.com", "Eve", role=Role.PILOT)
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, alice)
        _login(app, client, "alice@example.com")

        r = client.post(
            f"/aircraft/{acid}/owners/{outsider}/payment", data={"amount": "100"}
        )
        assert r.status_code == 404

    def test_404_for_non_shared_ownership_tenant(self, app, client):
        tid = _make_tenant(app, operating_model=OperatingModel.SOLE_OPERATOR)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _login(app, client, "alice@example.com")
        r = client.post(
            f"/aircraft/{acid}/owners/{uid}/payment", data={"amount": "100"}
        )
        assert r.status_code == 404

    def test_403_for_non_owner_role(self, app, client):
        tid = _make_tenant(app)
        pilot_uid = _make_user(app, tid, "pilot@example.com", "Pilot", role=Role.PILOT)
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, pilot_uid)
        with client.session_transaction() as sess:
            sess["user_id"] = pilot_uid
        r = client.post(
            f"/aircraft/{acid}/owners/{pilot_uid}/payment", data={"amount": "100"}
        )
        assert r.status_code == 403


# ── Reverse ────────────────────────────────────────────────────────────────────


class TestReverseEntry:
    def test_reverse_nets_to_zero(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        _login(app, client, "alice@example.com")

        client.post(f"/aircraft/{acid}/owners/{uid}/payment", data={"amount": "100"})
        with app.app_context():
            acc = _account(tid, uid, acid)
            entry_id = LedgerEntry.query.filter_by(account_id=acc.id).first().id

        r = client.post(
            f"/aircraft/{acid}/owners/{uid}/entries/{entry_id}/reverse",
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            assert _balance(tid, uid, acid) == Decimal("0.00")

    def test_double_reverse_flashes_service_error(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        _login(app, client, "alice@example.com")

        client.post(f"/aircraft/{acid}/owners/{uid}/payment", data={"amount": "100"})
        with app.app_context():
            acc = _account(tid, uid, acid)
            entry_id = LedgerEntry.query.filter_by(account_id=acc.id).first().id

        client.post(f"/aircraft/{acid}/owners/{uid}/entries/{entry_id}/reverse")
        r = client.post(
            f"/aircraft/{acid}/owners/{uid}/entries/{entry_id}/reverse",
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"already been reversed" in r.data

    def test_reverse_of_a_reversal_flashes_service_error(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        _login(app, client, "alice@example.com")

        client.post(f"/aircraft/{acid}/owners/{uid}/payment", data={"amount": "100"})
        with app.app_context():
            acc = _account(tid, uid, acid)
            entry_id = LedgerEntry.query.filter_by(account_id=acc.id).first().id

        client.post(f"/aircraft/{acid}/owners/{uid}/entries/{entry_id}/reverse")
        with app.app_context():
            acc = _account(tid, uid, acid)
            reversal_entry = LedgerEntry.query.filter_by(reverses_id=entry_id).first()
            reversal_id = reversal_entry.id

        r = client.post(
            f"/aircraft/{acid}/owners/{uid}/entries/{reversal_id}/reverse",
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"Cannot reverse a reversal" in r.data

    def test_recorded_by_stored_on_reversal(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        alice_uid = _login(app, client, "alice@example.com")

        client.post(f"/aircraft/{acid}/owners/{uid}/payment", data={"amount": "100"})
        with app.app_context():
            acc = _account(tid, uid, acid)
            entry_id = LedgerEntry.query.filter_by(account_id=acc.id).first().id

        client.post(f"/aircraft/{acid}/owners/{uid}/entries/{entry_id}/reverse")
        with app.app_context():
            reversal = LedgerEntry.query.filter_by(reverses_id=entry_id).first()
            assert reversal.created_by_id == alice_uid

    def test_entry_from_another_account_404(self, app, client):
        tid = _make_tenant(app)
        alice = _make_user(app, tid, "alice@example.com", "Alice")
        bob = _make_user(app, tid, "bob@example.com", "Bob")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, alice, share_pct=50)
        _add_owner(app, acid, bob, share_pct=50)
        _login(app, client, "alice@example.com")

        client.post(f"/aircraft/{acid}/owners/{bob}/payment", data={"amount": "100"})
        with app.app_context():
            bob_acc = _account(tid, bob, acid)
            entry_id = LedgerEntry.query.filter_by(account_id=bob_acc.id).first().id

        # Try to reverse Bob's entry via Alice's account-scoped URL
        r = client.post(f"/aircraft/{acid}/owners/{alice}/entries/{entry_id}/reverse")
        assert r.status_code == 404

    def test_404_for_non_shared_ownership_tenant(self, app, client):
        tid = _make_tenant(app, operating_model=OperatingModel.SOLE_OPERATOR)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _login(app, client, "alice@example.com")
        r = client.post(f"/aircraft/{acid}/owners/{uid}/entries/999/reverse")
        assert r.status_code == 404

    def test_403_for_non_owner_role(self, app, client):
        tid = _make_tenant(app)
        pilot_uid = _make_user(app, tid, "pilot@example.com", "Pilot", role=Role.PILOT)
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, pilot_uid)
        with client.session_transaction() as sess:
            sess["user_id"] = pilot_uid
        r = client.post(f"/aircraft/{acid}/owners/{pilot_uid}/entries/999/reverse")
        assert r.status_code == 403


# ── Dashboard: reverse button visibility ─────────────────────────────────────


class TestReverseButtonVisibility:
    def test_reverse_button_shown_for_live_payment(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        _login(app, client, "alice@example.com")

        client.post(f"/aircraft/{acid}/owners/{uid}/payment", data={"amount": "100"})
        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert b"Reverse" in r.data

    def test_reverse_button_absent_after_reversal(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        _login(app, client, "alice@example.com")

        client.post(f"/aircraft/{acid}/owners/{uid}/payment", data={"amount": "100"})
        with app.app_context():
            acc = _account(tid, uid, acid)
            entry_id = LedgerEntry.query.filter_by(account_id=acc.id).first().id
        client.post(f"/aircraft/{acid}/owners/{uid}/entries/{entry_id}/reverse")

        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert b"Reverse" not in r.data

    def test_reverse_button_absent_for_non_payment_entries(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid, buy_in=500)
        _login(app, client, "alice@example.com")

        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert b"Buy-in" in r.data
        assert b"Reverse" not in r.data
