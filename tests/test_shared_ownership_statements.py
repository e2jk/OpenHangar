"""
Tests for Phase 39f: co-owner statements (admin HTML/CSV + self-service).

See docs/implementation_plan.md, Phase 39 ("Shared Ownership").
"""

from datetime import date
from decimal import Decimal

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AircraftOwner,
    OperatingModel,
    Role,
    Tenant,
    TenantProfile,
    TenantUser,
    User,
    UserAircraftAccess,
    db,
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


def _grant_access(app, user_id, aircraft_id):
    with app.app_context():
        db.session.add(UserAircraftAccess(user_id=user_id, aircraft_id=aircraft_id))
        db.session.commit()


# ── Admin HTML statement page ─────────────────────────────────────────────────


class TestOwnerAccountRoute:
    def test_shows_statement_with_opening_and_closing(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid, buy_in=1000)
        _login(app, client, "alice@example.com")

        with app.app_context():
            from services.co_owner_billing import run_co_owner_billing_pass

            ac = db.session.get(Aircraft, acid)
            run_co_owner_billing_pass(ac)
            db.session.commit()

        r = client.get(f"/aircraft/{acid}/owners/{uid}/account")
        assert r.status_code == 200
        assert b"Alice" in r.data
        assert b"-1000.00" in r.data

    def test_404_for_non_shared_ownership_tenant(self, app, client):
        tid = _make_tenant(app, operating_model=OperatingModel.SOLE_OPERATOR)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}/owners/{uid}/account")
        assert r.status_code == 404

    def test_404_for_non_co_owner_target(self, app, client):
        tid = _make_tenant(app)
        alice = _make_user(app, tid, "alice@example.com", "Alice")
        outsider = _make_user(app, tid, "eve@example.com", "Eve", role=Role.PILOT)
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, alice)
        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}/owners/{outsider}/account")
        assert r.status_code == 404

    def test_tenant_isolation_404(self, app, client):
        tid1 = _make_tenant(app)
        _make_user(app, tid1, "alice@example.com", "Alice")
        tid2 = _make_tenant(app)
        acid2 = _make_aircraft(app, tid2, "OO-OTH")
        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid2}/owners/999/account")
        assert r.status_code == 404

    def test_403_for_non_owner_role(self, app, client):
        tid = _make_tenant(app)
        pilot_uid = _make_user(app, tid, "pilot@example.com", "Pilot", role=Role.PILOT)
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, pilot_uid)
        with client.session_transaction() as sess:
            sess["user_id"] = pilot_uid
        r = client.get(f"/aircraft/{acid}/owners/{pilot_uid}/account")
        assert r.status_code == 403

    def test_period_filters_entries(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2020, 1, 1))
        _add_owner(app, acid, uid, buy_in=1000)
        _login(app, client, "alice@example.com")

        with app.app_context():
            from services.co_owner_billing import run_co_owner_billing_pass

            ac = db.session.get(Aircraft, acid)
            run_co_owner_billing_pass(ac)
            db.session.commit()

        # 1-month period excludes the old buy-in from the itemised lines,
        # but it's folded into the opening balance instead.
        r = client.get(f"/aircraft/{acid}/owners/{uid}/account?period=1")
        assert r.status_code == 200
        assert b"-1000.00" in r.data  # opening balance still reflects it


# ── CSV export ─────────────────────────────────────────────────────────────────


class TestOwnerStatementCsv:
    def test_404_for_non_shared_ownership_tenant(self, app, client):
        tid = _make_tenant(app, operating_model=OperatingModel.SOLE_OPERATOR)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}/owners/{uid}/account/statement.csv")
        assert r.status_code == 404

    def test_content_disposition_and_filename(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, "OO-CSV")
        _add_owner(app, acid, uid, buy_in=100)
        _login(app, client, "alice@example.com")

        r = client.get(f"/aircraft/{acid}/owners/{uid}/account/statement.csv")
        assert r.status_code == 200
        assert r.mimetype == "text/csv"
        disposition = r.headers["Content-Disposition"]
        assert "attachment" in disposition
        assert f"co_owner_statement_OO-CSV_{uid}_" in disposition

    def test_export_totals_and_metadata(self, app, client):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, uid, buy_in=1000)
        _login(app, client, "alice@example.com")

        with app.app_context():
            from services.co_owner_billing import run_co_owner_billing_pass

            ac = db.session.get(Aircraft, acid)
            run_co_owner_billing_pass(ac)
            db.session.commit()

        r = client.get(f"/aircraft/{acid}/owners/{uid}/account/statement.csv")
        csv_text = r.data.decode()
        assert "Export date" in csv_text
        assert "Exporter" in csv_text
        assert "Period" in csv_text
        assert "Alice" in csv_text
        assert "Opening balance" in csv_text
        assert "Closing balance" in csv_text
        assert "-1000.00" in csv_text

    def test_404_for_non_co_owner_target(self, app, client):
        tid = _make_tenant(app)
        alice = _make_user(app, tid, "alice@example.com", "Alice")
        outsider = _make_user(app, tid, "eve@example.com", "Eve", role=Role.PILOT)
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, alice)
        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}/owners/{outsider}/account/statement.csv")
        assert r.status_code == 404


# ── Self-service: my-share ─────────────────────────────────────────────────────


class TestMyShare:
    def test_co_owner_sees_own_data(self, app, client):
        tid = _make_tenant(app)
        alice = _make_user(app, tid, "alice@example.com", "Alice", role=Role.PILOT)
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, alice, buy_in=750)
        _grant_access(app, alice, acid)
        _login(app, client, "alice@example.com")

        with app.app_context():
            from services.co_owner_billing import run_co_owner_billing_pass

            ac = db.session.get(Aircraft, acid)
            run_co_owner_billing_pass(ac)
            db.session.commit()

        r = client.get(f"/aircraft/{acid}/my-share")
        assert r.status_code == 200
        assert b"-750.00" in r.data

    def test_non_co_owner_gets_404(self, app, client):
        tid = _make_tenant(app)
        _make_user(app, tid, "eve@example.com", "Eve", role=Role.PILOT)
        acid = _make_aircraft(app, tid)
        _login(app, client, "eve@example.com")
        r = client.get(f"/aircraft/{acid}/my-share")
        assert r.status_code == 404

    def test_admin_owner_role_but_not_a_co_owner_gets_404(self, app, client):
        """Being Owner/Admin role doesn't grant access to a share you don't
        actually hold — my-share is scoped to *actual* co-owners only."""
        tid = _make_tenant(app)
        _make_user(app, tid, "admin@example.com", "Admin")
        alice = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, alice)
        _login(app, client, "admin@example.com")
        r = client.get(f"/aircraft/{acid}/my-share")
        assert r.status_code == 404

    def test_another_co_owners_data_not_reachable_via_admin_url(self, app, client):
        """A co-owner cannot use the admin-scoped account URL to view a
        different co-owner's data — that route requires Owner/Admin role."""
        tid = _make_tenant(app)
        alice = _make_user(app, tid, "alice@example.com", "Alice", role=Role.PILOT)
        bob = _make_user(app, tid, "bob@example.com", "Bob", role=Role.PILOT)
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, alice, share_pct=50)
        _add_owner(app, acid, bob, share_pct=50)
        _login(app, client, "alice@example.com")

        r = client.get(f"/aircraft/{acid}/owners/{bob}/account")
        assert r.status_code == 403

    def test_tenant_isolation_404(self, app, client):
        tid1 = _make_tenant(app)
        _make_user(app, tid1, "alice@example.com", "Alice")
        tid2 = _make_tenant(app)
        acid2 = _make_aircraft(app, tid2, "OO-OTH")
        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid2}/my-share")
        assert r.status_code == 404


class TestMyShareStatementCsv:
    def test_downloads_own_csv(self, app, client):
        tid = _make_tenant(app)
        alice = _make_user(app, tid, "alice@example.com", "Alice", role=Role.PILOT)
        acid = _make_aircraft(app, tid, "OO-MYS")
        _add_owner(app, acid, alice, buy_in=300)
        _grant_access(app, alice, acid)
        _login(app, client, "alice@example.com")

        with app.app_context():
            from services.co_owner_billing import run_co_owner_billing_pass

            ac = db.session.get(Aircraft, acid)
            run_co_owner_billing_pass(ac)
            db.session.commit()

        r = client.get(f"/aircraft/{acid}/my-share/statement.csv")
        assert r.status_code == 200
        assert r.mimetype == "text/csv"
        assert "my_share_statement_OO-MYS_" in r.headers["Content-Disposition"]
        assert "-300.00" in r.data.decode()

    def test_non_co_owner_gets_404(self, app, client):
        tid = _make_tenant(app)
        _make_user(app, tid, "eve@example.com", "Eve", role=Role.PILOT)
        acid = _make_aircraft(app, tid)
        _login(app, client, "eve@example.com")
        r = client.get(f"/aircraft/{acid}/my-share/statement.csv")
        assert r.status_code == 404

    def test_admin_role_but_not_a_co_owner_gets_404(self, app, client):
        """An Owner/Admin role bypasses the aircraft-access grant check but
        must still 404 here — my-share is scoped to actual co-owners."""
        tid = _make_tenant(app)
        _make_user(app, tid, "admin@example.com", "Admin")
        acid = _make_aircraft(app, tid)
        _login(app, client, "admin@example.com")
        r = client.get(f"/aircraft/{acid}/my-share/statement.csv")
        assert r.status_code == 404


# ── Aircraft detail: "My share" link visibility ──────────────────────────────


class TestMyShareLinkVisibility:
    def test_link_shown_for_co_owner(self, app, client):
        tid = _make_tenant(app)
        alice = _make_user(app, tid, "alice@example.com", "Alice", role=Role.PILOT)
        acid = _make_aircraft(app, tid)
        _add_owner(app, acid, alice)
        _grant_access(app, alice, acid)
        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}")
        assert b"My share" in r.data

    def test_link_absent_for_non_co_owner(self, app, client):
        tid = _make_tenant(app)
        _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}")
        assert b"My share" not in r.data

    def test_link_absent_on_non_shared_ownership_tenant(self, app, client):
        tid = _make_tenant(app, operating_model=OperatingModel.SOLE_OPERATOR)
        _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid)
        _login(app, client, "alice@example.com")
        r = client.get(f"/aircraft/{acid}")
        assert b"My share" not in r.data
