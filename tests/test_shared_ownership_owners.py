"""
Tests for Phase 39a: AircraftOwner model, manage-owners page, and the
aircraft-detail ownership breakdown card.

See docs/implementation_plan.md, Phase 39 ("Shared Ownership").
"""

from datetime import date
from decimal import Decimal

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]

from aircraft.co_owner_form_parsing import (  # pyright: ignore[reportMissingImports]
    parse_owners_form,
)
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AircraftOwner,
    OperatingModel,
    Role,
    Tenant,
    TenantProfile,
    TenantUser,
    User,
    db,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


class _FakeForm:
    """Minimal .get()/.getlist() stub mirroring werkzeug's ImmutableMultiDict,
    for direct unit tests of parse_owners_form()."""

    def __init__(self, data: dict[str, list[str]]):
        self._data = data

    def get(self, name: str, default: str = "") -> str:
        vals = self._data.get(name)
        return vals[0] if vals else default

    def getlist(self, name: str) -> list[str]:
        return self._data.get(name, [])


def _create_user_and_tenant(
    app, email="owner@example.com", operating_model=OperatingModel.SHARED_OWNERSHIP
):
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
        user = User(
            email=email,
            password_hash=_pw_hash.hash("testpassword123"),
            is_active=True,
            name="Alice Admin",
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()
        return user.id, tenant.id


def _add_tenant_user(app, tenant_id, email, name, role=Role.OWNER):
    with app.app_context():
        user = User(
            email=email,
            password_hash=_pw_hash.hash("testpassword123"),
            is_active=True,
            name=name,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant_id, role=role))
        db.session.commit()
        return user.id


def _login(app, client, email="owner@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _add_aircraft(app, tenant_id, registration="OO-CO1"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration=registration, make="Cessna", model="172S"
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


# ── Direct unit tests: parse_owners_form ─────────────────────────────────────


class TestParseOwnersForm:
    def test_valid_three_owners_sum_to_100(self):
        form = _FakeForm(
            {
                "owner_user_id[]": ["1", "2", "3"],
                "owner_share_pct[]": ["50", "30", "20"],
                "owner_buy_in_amount[]": ["1000", "600", "400"],
            }
        )
        rows, _start, _rate, errors = parse_owners_form(form)
        assert errors == []
        assert len(rows) == 3
        assert sum((r["share_pct"] for r in rows), Decimal("0")) == Decimal("100")

    def test_sum_not_100_rejected(self):
        form = _FakeForm(
            {
                "owner_user_id[]": ["1", "2"],
                "owner_share_pct[]": ["60", "30"],
                "owner_buy_in_amount[]": ["0", "0"],
            }
        )
        rows, _start, _rate, errors = parse_owners_form(form)
        assert any("100" in e for e in errors)

    def test_single_owner_at_100_valid(self):
        form = _FakeForm(
            {
                "owner_user_id[]": ["1"],
                "owner_share_pct[]": ["100"],
                "owner_buy_in_amount[]": ["500"],
            }
        )
        rows, _start, _rate, errors = parse_owners_form(form)
        assert errors == []
        assert len(rows) == 1

    def test_zero_rows_clears_and_is_valid(self):
        form = _FakeForm({"owner_user_id[]": ["", ""]})
        rows, _start, _rate, errors = parse_owners_form(form)
        assert rows == []
        assert errors == []

    def test_duplicate_user_rejected(self):
        form = _FakeForm(
            {
                "owner_user_id[]": ["1", "1"],
                "owner_share_pct[]": ["50", "50"],
            }
        )
        _rows, _start, _rate, errors = parse_owners_form(form)
        assert any("once" in e for e in errors)

    def test_share_zero_rejected(self):
        form = _FakeForm({"owner_user_id[]": ["1"], "owner_share_pct[]": ["0"]})
        _rows, _start, _rate, errors = parse_owners_form(form)
        assert errors != []

    def test_share_negative_rejected(self):
        form = _FakeForm({"owner_user_id[]": ["1"], "owner_share_pct[]": ["-5"]})
        _rows, _start, _rate, errors = parse_owners_form(form)
        assert errors != []

    def test_share_over_100_rejected(self):
        form = _FakeForm({"owner_user_id[]": ["1"], "owner_share_pct[]": ["100.01"]})
        _rows, _start, _rate, errors = parse_owners_form(form)
        assert errors != []

    def test_share_more_than_two_decimals_rejected(self):
        form = _FakeForm({"owner_user_id[]": ["1"], "owner_share_pct[]": ["33.333"]})
        _rows, _start, _rate, errors = parse_owners_form(form)
        assert errors != []

    def test_share_non_numeric_rejected(self):
        form = _FakeForm({"owner_user_id[]": ["1"], "owner_share_pct[]": ["abc"]})
        _rows, _start, _rate, errors = parse_owners_form(form)
        assert errors != []

    def test_share_infinite_rejected(self):
        form = _FakeForm({"owner_user_id[]": ["1"], "owner_share_pct[]": ["inf"]})
        _rows, _start, _rate, errors = parse_owners_form(form)
        assert errors != []

    def test_share_nan_rejected(self):
        form = _FakeForm({"owner_user_id[]": ["1"], "owner_share_pct[]": ["nan"]})
        _rows, _start, _rate, errors = parse_owners_form(form)
        assert errors != []

    def test_negative_buy_in_rejected(self):
        form = _FakeForm(
            {
                "owner_user_id[]": ["1"],
                "owner_share_pct[]": ["100"],
                "owner_buy_in_amount[]": ["-1"],
            }
        )
        _rows, _start, _rate, errors = parse_owners_form(form)
        assert errors != []

    def test_buy_in_defaults_to_zero_when_blank(self):
        form = _FakeForm({"owner_user_id[]": ["1"], "owner_share_pct[]": ["100"]})
        rows, _start, _rate, errors = parse_owners_form(form)
        assert errors == []
        assert rows[0]["buy_in_amount"] == Decimal("0")

    def test_remove_checkbox_skips_row(self):
        form = _FakeForm(
            {
                "owner_user_id[]": ["1", "2"],
                "owner_share_pct[]": ["50", "100"],
                "owner_remove[]": ["0"],
            }
        )
        rows, _start, _rate, errors = parse_owners_form(form)
        assert errors == []
        assert len(rows) == 1
        assert rows[0]["user_id"] == 2

    def test_invalid_user_id_rejected(self):
        form = _FakeForm(
            {"owner_user_id[]": ["not-a-number"], "owner_share_pct[]": ["100"]}
        )
        _rows, _start, _rate, errors = parse_owners_form(form)
        assert any("Invalid owner" in e for e in errors)

    def test_billing_start_parsed(self):
        form = _FakeForm(
            {
                "owner_user_id[]": ["1"],
                "owner_share_pct[]": ["100"],
                "co_owner_billing_start": ["2026-01-01"],
            }
        )
        _rows, start, _rate, errors = parse_owners_form(form)
        assert errors == []
        assert start == date(2026, 1, 1)

    def test_billing_start_invalid_is_error(self):
        form = _FakeForm(
            {
                "owner_user_id[]": ["1"],
                "owner_share_pct[]": ["100"],
                "co_owner_billing_start": ["garbage"],
            }
        )
        _rows, _start, _rate, errors = parse_owners_form(form)
        assert any("Billing start" in e for e in errors)

    def test_hourly_rate_parsed(self):
        form = _FakeForm(
            {
                "owner_user_id[]": ["1"],
                "owner_share_pct[]": ["100"],
                "co_owner_hourly_rate": ["120.50"],
            }
        )
        _rows, _start, rate, errors = parse_owners_form(form)
        assert errors == []
        assert rate == Decimal("120.50")

    def test_hourly_rate_negative_is_error(self):
        form = _FakeForm(
            {
                "owner_user_id[]": ["1"],
                "owner_share_pct[]": ["100"],
                "co_owner_hourly_rate": ["-1"],
            }
        )
        _rows, _start, rate, errors = parse_owners_form(form)
        assert rate is None
        assert any("Hourly rate" in e for e in errors)

    def test_hourly_rate_blank_is_none_no_error(self):
        form = _FakeForm({"owner_user_id[]": ["1"], "owner_share_pct[]": ["100"]})
        _rows, _start, rate, errors = parse_owners_form(form)
        assert rate is None
        assert errors == []


# ── Route: manage_owners ──────────────────────────────────────────────────────


class TestManageOwnersRoute:
    def test_get_shows_form(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/owners")
        assert r.status_code == 200
        assert b"Manage owners" in r.data

    def test_404_for_non_shared_ownership_tenant(self, app, client):
        uid, tid = _create_user_and_tenant(
            app, operating_model=OperatingModel.SOLE_OPERATOR
        )
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/owners")
        assert r.status_code == 404

    def test_403_for_non_owner_role(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        pilot_uid = _add_tenant_user(
            app, tid, "pilot@example.com", "Pat Pilot", role=Role.PILOT
        )
        with client.session_transaction() as sess:
            sess["user_id"] = pilot_uid
        r = client.get(f"/aircraft/{acid}/owners")
        assert r.status_code == 403

    def test_tenant_isolation_404(self, app, client):
        _create_user_and_tenant(app)
        _, other_tid = _create_user_and_tenant(app, email="other@example.com")
        other_acid = _add_aircraft(app, other_tid, registration="OO-OTH")
        _login(app, client)
        r = client.get(f"/aircraft/{other_acid}/owners")
        assert r.status_code == 404

    def test_post_creates_owners_summing_to_100(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        bob = _add_tenant_user(app, tid, "bob@example.com", "Bob Owner")
        carol = _add_tenant_user(app, tid, "carol@example.com", "Carol Owner")
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/owners",
            data={
                "owner_user_id[]": [str(uid), str(bob), str(carol)],
                "owner_share_pct[]": ["50", "30", "20"],
                "owner_buy_in_amount[]": ["1000", "600", "400"],
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            owners = AircraftOwner.query.filter_by(aircraft_id=acid).all()
            assert len(owners) == 3
            total = sum(o.share_pct for o in owners)
            assert total == 100

    def test_post_rejects_sum_not_100(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/owners",
            data={
                "owner_user_id[]": [str(uid)],
                "owner_share_pct[]": ["60"],
            },
        )
        assert r.status_code == 200
        assert b"100" in r.data
        with app.app_context():
            assert AircraftOwner.query.filter_by(aircraft_id=acid).count() == 0

    def test_billing_start_defaults_to_today_on_first_save(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/owners",
            data={
                "owner_user_id[]": [str(uid)],
                "owner_share_pct[]": ["100"],
            },
        )
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            assert ac.co_owner_billing_start == date.today()

    def test_billing_start_not_overwritten_on_later_save_when_left_blank(
        self, app, client
    ):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/owners",
            data={
                "owner_user_id[]": [str(uid)],
                "owner_share_pct[]": ["100"],
                "co_owner_billing_start": "2020-01-01",
            },
        )
        client.post(
            f"/aircraft/{acid}/owners",
            data={
                "owner_user_id[]": [str(uid)],
                "owner_share_pct[]": ["100"],
                "owner_buy_in_amount[]": ["50"],
            },
        )
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            assert ac.co_owner_billing_start == date(2020, 1, 1)

    def test_zero_rows_clears_owner_set(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/owners",
            data={"owner_user_id[]": [str(uid)], "owner_share_pct[]": ["100"]},
        )
        client.post(f"/aircraft/{acid}/owners", data={"owner_user_id[]": [""]})
        with app.app_context():
            assert AircraftOwner.query.filter_by(aircraft_id=acid).count() == 0

    def test_kept_owner_row_id_preserved_on_edit(self, app, client):
        """Editing an existing owner's share must update the row in place,
        not delete+recreate — the billing pass links ledger entries to
        AircraftOwner.id, so a changed id would orphan prior postings."""
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/owners",
            data={"owner_user_id[]": [str(uid)], "owner_share_pct[]": ["100"]},
        )
        with app.app_context():
            original_id = AircraftOwner.query.filter_by(aircraft_id=acid).first().id

        client.post(
            f"/aircraft/{acid}/owners",
            data={
                "owner_user_id[]": [str(uid)],
                "owner_share_pct[]": ["100"],
                "owner_buy_in_amount[]": ["999"],
            },
        )
        with app.app_context():
            owner = AircraftOwner.query.filter_by(aircraft_id=acid).first()
            assert owner.id == original_id
            assert owner.buy_in_amount == 999

    def test_legacy_data_escape_hatch(self, app, client):
        """A tenant whose operating model was switched away from
        shared_ownership can still reach the manage page while owner rows
        exist, and clearing them makes it 404 afterwards."""
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            db.session.add(
                AircraftOwner(
                    aircraft_id=acid, user_id=uid, share_pct=100, buy_in_amount=0
                )
            )
            profile = TenantProfile.query.filter_by(tenant_id=tid).first()
            profile.operating_model = OperatingModel.SOLE_OPERATOR
            db.session.commit()
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/owners")
        assert r.status_code == 200

        client.post(f"/aircraft/{acid}/owners", data={"owner_user_id[]": [""]})
        r2 = client.get(f"/aircraft/{acid}/owners")
        assert r2.status_code == 404


# ── Aircraft detail: ownership breakdown card ────────────────────────────────


class TestOwnershipBreakdownCard:
    def test_card_renders_owner_rows(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        bob = _add_tenant_user(app, tid, "bob@example.com", "Bob Owner")
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/owners",
            data={
                "owner_user_id[]": [str(uid), str(bob)],
                "owner_share_pct[]": ["60", "40"],
                "owner_buy_in_amount[]": ["100", "200"],
            },
        )
        r = client.get(f"/aircraft/{acid}")
        assert r.status_code == 200
        assert b"Bob Owner" in r.data
        assert b"60" in r.data

    def test_card_absent_for_other_operating_models(self, app, client):
        uid, tid = _create_user_and_tenant(
            app, operating_model=OperatingModel.SOLE_OPERATOR
        )
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}")
        assert r.status_code == 200
        assert b"Ownership" not in r.data
        assert b"Manage owners" not in r.data

    def test_no_trace_for_sole_operator_tenant(self, app, client):
        """Zero visible/behavioural trace on an instance that never used
        shared ownership: no Phase 39 strings on the detail page, and the
        edit form is byte-for-byte unaffected (no new fields added there)."""
        uid, tid = _create_user_and_tenant(
            app, operating_model=OperatingModel.SOLE_OPERATOR
        )
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}")
        assert r.status_code == 200
        for needle in (b"Ownership", b"Manage owners", b"Billing dashboard"):
            assert needle not in r.data

        r_owners = client.get(f"/aircraft/{acid}/owners")
        assert r_owners.status_code == 404

        r_edit = client.get(f"/aircraft/{acid}/edit")
        assert r_edit.status_code == 200
        assert b"co_owner" not in r_edit.data
