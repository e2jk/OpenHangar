"""
Tests for Phase 39e: co-owner valuation snapshots.

See docs/phase39_shared_ownership_spec.md ("39e — Valuation snapshots").
"""

from datetime import date
from decimal import Decimal

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AircraftOwner,
    BillingAccountKind,
    CoOwnerValuationSnapshot,
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


# ── Record snapshot ────────────────────────────────────────────────────────────


class TestRecordValuationSnapshot:
    def test_writes_one_row_per_owner_with_correct_balance_and_share(self, app, client):
        tid = _make_tenant(app)
        alice = _make_user(app, tid, "alice@example.com", "Alice")
        bob = _make_user(app, tid, "bob@example.com", "Bob")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, alice, share_pct=60, buy_in=600)
        _add_owner(app, acid, bob, share_pct=40, buy_in=400)
        _login(app, client, "alice@example.com")

        r = client.post(
            f"/aircraft/{acid}/owners/valuation",
            data={"date": "2026-03-01"},
            follow_redirects=False,
        )
        assert r.status_code == 302

        with app.app_context():
            snaps = CoOwnerValuationSnapshot.query.filter_by(aircraft_id=acid).all()
            assert len(snaps) == 2
            by_user = {s.user_id: s for s in snaps}
            assert by_user[alice].capital_balance == Decimal("600.00")
            assert by_user[alice].share_pct == Decimal("60.00")
            assert by_user[bob].capital_balance == Decimal("400.00")
            assert by_user[bob].share_pct == Decimal("40.00")
            assert all(s.valuation_date == date(2026, 3, 1) for s in snaps)

    def test_immutable_after_further_postings(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid, buy_in=1000)
        _login(app, client, "alice@example.com")

        client.post(f"/aircraft/{acid}/owners/valuation", data={"date": "2026-03-01"})
        with app.app_context():
            snap = CoOwnerValuationSnapshot.query.filter_by(aircraft_id=acid).first()
            first_balance = snap.capital_balance
            assert first_balance == Decimal("1000.00")

        # Post a new charge after the snapshot
        with app.app_context():
            from models import BillingAccount

            acc = BillingAccount.query.filter_by(
                tenant_id=tid,
                user_id=uid,
                kind=BillingAccountKind.CO_OWNER,
                aircraft_id=acid,
            ).first()
            BillingService.post(
                acc,
                LedgerEntryType.CHARGE,
                Decimal("200"),
                "Later charge",
                date(2026, 3, 15),
                source_type="test",
                source_id=1,
            )
            db.session.commit()

        with app.app_context():
            snap = CoOwnerValuationSnapshot.query.filter_by(aircraft_id=acid).first()
            assert snap.capital_balance == first_balance

        # A new snapshot reflects the change
        client.post(f"/aircraft/{acid}/owners/valuation", data={"date": "2026-03-20"})
        with app.app_context():
            new_snap = (
                CoOwnerValuationSnapshot.query.filter_by(aircraft_id=acid)
                .order_by(CoOwnerValuationSnapshot.id.desc())
                .first()
            )
            assert new_snap.capital_balance == Decimal("800.00")

    def test_as_of_excludes_entries_dated_after_valuation_date(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid, buy_in=1000)
        _login(app, client, "alice@example.com")

        with app.app_context():
            acc = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.CO_OWNER, aircraft_id=acid
            )
            BillingService.post(
                acc,
                LedgerEntryType.CHARGE,
                Decimal("300"),
                "Future charge",
                date(2026, 6, 1),
                source_type="test",
                source_id=1,
            )
            db.session.commit()

        r = client.post(
            f"/aircraft/{acid}/owners/valuation", data={"date": "2026-03-01"}
        )
        assert r.status_code == 302
        with app.app_context():
            snap = CoOwnerValuationSnapshot.query.filter_by(aircraft_id=acid).first()
            # Only the buy-in (posted at billing_start, before the valuation
            # date) counts — the future-dated charge is excluded.
            assert snap.capital_balance == Decimal("1000.00")

    def test_date_defaults_to_today_when_blank(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        _login(app, client, "alice@example.com")

        client.post(f"/aircraft/{acid}/owners/valuation", data={})
        with app.app_context():
            snap = CoOwnerValuationSnapshot.query.filter_by(aircraft_id=acid).first()
            assert snap.valuation_date == date.today()

    def test_invalid_date_falls_back_to_today(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        _login(app, client, "alice@example.com")

        client.post(f"/aircraft/{acid}/owners/valuation", data={"date": "not-a-date"})
        with app.app_context():
            snap = CoOwnerValuationSnapshot.query.filter_by(aircraft_id=acid).first()
            assert snap.valuation_date == date.today()

    def test_note_stored(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        _login(app, client, "alice@example.com")

        client.post(
            f"/aircraft/{acid}/owners/valuation",
            data={"date": "2026-03-01", "note": "End of year"},
        )
        with app.app_context():
            snap = CoOwnerValuationSnapshot.query.filter_by(aircraft_id=acid).first()
            assert snap.note == "End of year"

    def test_recorder_stored(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        alice_uid = _login(app, client, "alice@example.com")

        client.post(f"/aircraft/{acid}/owners/valuation", data={"date": "2026-03-01"})
        with app.app_context():
            snap = CoOwnerValuationSnapshot.query.filter_by(aircraft_id=acid).first()
            assert snap.created_by_id == alice_uid

    def test_404_for_non_shared_ownership_tenant(self, app, client):
        tid = _make_tenant(app, operating_model=OperatingModel.SOLE_OPERATOR)
        _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _login(app, client, "alice@example.com")
        r = client.post(f"/aircraft/{acid}/owners/valuation", data={})
        assert r.status_code == 404

    def test_tenant_isolation_404(self, app, client):
        tid1 = _make_tenant(app)
        _make_user(app, tid1, "alice@example.com", "Alice")
        tid2 = _make_tenant(app)
        acid2 = _make_aircraft(app, tid2, "OO-OTH")
        _login(app, client, "alice@example.com")
        r = client.post(f"/aircraft/{acid2}/owners/valuation", data={})
        assert r.status_code == 404

    def test_403_for_non_owner_role(self, app, client):
        tid = _make_tenant(app)
        pilot_uid = _make_user(app, tid, "pilot@example.com", "Pilot", role=Role.PILOT)
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, pilot_uid)
        with client.session_transaction() as sess:
            sess["user_id"] = pilot_uid
        r = client.post(f"/aircraft/{acid}/owners/valuation", data={})
        assert r.status_code == 403


# ── Dashboard: history table ──────────────────────────────────────────────────


class TestValuationHistoryOnDashboard:
    def test_history_shown_grouped_by_date(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid, buy_in=500)
        _login(app, client, "alice@example.com")

        client.post(f"/aircraft/{acid}/owners/valuation", data={"date": "2026-01-31"})
        client.post(f"/aircraft/{acid}/owners/valuation", data={"date": "2026-02-28"})

        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert r.status_code == 200
        assert b"2026-01-31" in r.data
        assert b"2026-02-28" in r.data

    def test_empty_state_shown_when_no_snapshots(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid)
        _login(app, client, "alice@example.com")

        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert b"No valuation snapshots recorded yet." in r.data
