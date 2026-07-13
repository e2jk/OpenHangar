"""
Tests for Phase 8: Cost tracking (Expense model, expenses blueprint, fuel on flight form).
"""

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from datetime import date, timedelta

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Expense,
    ExpenseCategory,
    ExpenseType,
    FlightEntry,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_user_and_tenant(app, email="pilot@example.com"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=_pw_hash.hash("pw"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="pilot@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _add_aircraft(app, tenant_id):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration="OO-TST", make="Cessna", model="172S"
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_expense(
    app,
    aircraft_id,
    expense_type=ExpenseType.FUEL,
    amount=100.0,
    exp_date=None,
    currency="EUR",
    quantity=None,
    unit=None,
    description=None,
    expense_category=None,
    coverage_start=None,
    coverage_end=None,
    flight_entry_id=None,
):
    with app.app_context():
        exp = Expense(
            aircraft_id=aircraft_id,
            date=exp_date or date.today(),
            expense_type=expense_type,
            amount=amount,
            currency=currency,
            quantity=quantity,
            unit=unit,
            description=description,
            flight_entry_id=flight_entry_id,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
        )
        if expense_category is not None:
            exp.expense_category = expense_category
        db.session.add(exp)
        db.session.commit()
        return exp.id


def _add_flight(app, aircraft_id, hobbs_start=100.0, hobbs_end=101.5, flight_date=None):
    with app.app_context():
        fe = FlightEntry(
            aircraft_id=aircraft_id,
            date=flight_date or date.today(),
            departure_icao="EBOS",
            arrival_icao="EBBR",
            flight_time_counter_start=hobbs_start,
            flight_time_counter_end=hobbs_end,
        )
        db.session.add(fe)
        db.session.commit()
        return fe.id


# ── Expense model ─────────────────────────────────────────────────────────────


class TestExpenseModel:
    def test_expense_stored_and_retrieved(self, app):
        _, tenant_id = _create_user_and_tenant(app, "model@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        exp_id = _add_expense(app, ac_id, amount=250.0, quantity=80.0, unit="L")
        with app.app_context():
            exp = db.session.get(Expense, exp_id)
            assert float(exp.amount) == 250.0
            assert exp.expense_type == ExpenseType.FUEL
            assert float(exp.quantity) == 80.0
            assert exp.unit == "L"

    def test_expense_deleted_with_aircraft(self, app):
        _, tenant_id = _create_user_and_tenant(app, "cascade@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        exp_id = _add_expense(app, ac_id)
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            db.session.delete(ac)
            db.session.commit()
            assert db.session.get(Expense, exp_id) is None

    def test_expense_type_constants(self):
        assert ExpenseType.FUEL == "fuel"
        assert ExpenseType.PARTS == "parts"
        assert ExpenseType.INSURANCE == "insurance"
        assert ExpenseType.OTHER == "other"
        assert len(ExpenseType.ALL) == 4
        assert len(ExpenseType.LABELS) == 4

    def test_expense_category_constants(self):
        assert ExpenseCategory.FIXED == "fixed"
        assert ExpenseCategory.OPERATING == "operating"
        assert ExpenseCategory.ALL == {"fixed", "operating"}
        assert ExpenseCategory.DEFAULTS[ExpenseType.INSURANCE] == ExpenseCategory.FIXED
        assert ExpenseCategory.DEFAULTS[ExpenseType.FUEL] == ExpenseCategory.OPERATING

    def test_expense_defaults_to_operating_category(self, app):
        _, tenant_id = _create_user_and_tenant(app, "defaultcat@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        exp_id = _add_expense(app, ac_id)
        with app.app_context():
            exp = db.session.get(Expense, exp_id)
            assert exp.expense_category == ExpenseCategory.OPERATING


# ── List expenses ─────────────────────────────────────────────────────────────


class TestListExpenses:
    def test_list_renders(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/expenses")
        assert resp.status_code == 200

    def test_list_shows_expense(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_expense(app, ac_id, amount=99.99, description="Test fuel")
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/expenses")
        assert b"Test fuel" in resp.data

    def test_list_filter_by_type(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_expense(app, ac_id, expense_type=ExpenseType.FUEL, description="Avgas-XYZ")
        _add_expense(
            app, ac_id, expense_type=ExpenseType.INSURANCE, description="PolicyABC"
        )
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/expenses?type=fuel")
        assert b"Avgas-XYZ" in resp.data
        assert b"PolicyABC" not in resp.data

    def test_list_filter_by_period(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        old_date = date.today() - timedelta(days=400)
        _add_expense(app, ac_id, exp_date=old_date, description="OldExpense")
        _add_expense(app, ac_id, description="RecentExpense")
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/expenses?period=12")
        assert b"OldExpense" not in resp.data
        assert b"RecentExpense" in resp.data

    def test_list_all_time(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        old_date = date.today() - timedelta(days=400)
        _add_expense(app, ac_id, exp_date=old_date, description="OldExpense")
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/expenses?period=0")
        assert b"OldExpense" in resp.data

    def test_list_cost_per_hour_shown(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_expense(app, ac_id, amount=150.0, exp_date=date.today())
        _add_flight(
            app, ac_id, hobbs_start=100.0, hobbs_end=102.0, flight_date=date.today()
        )
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/expenses?period=0")
        assert b"75.00" in resp.data  # 150 / 2 h = 75/h

    def test_compute_stats_total_cost_and_cost_per_hour_exact_values(self, app):
        """total_cost and cost_per_hour are never independently pinned by the
        route-level tests above (which only check a pre-rounded rate) — call
        _compute_stats directly with non-round figures to catch a wrong sum
        or a wrong division."""
        from expenses.routes import _compute_stats  # pyright: ignore[reportMissingImports]

        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_expense(app, ac_id, amount=120.50, exp_date=date.today())
        _add_expense(app, ac_id, amount=45.25, exp_date=date.today())
        _add_flight(
            app, ac_id, hobbs_start=100.0, hobbs_end=102.5, flight_date=date.today()
        )
        with app.app_context():
            expenses = Expense.query.filter_by(aircraft_id=ac_id).all()
            total_cost, cost_per_hour, _label = _compute_stats(expenses, ac_id, 0)
        assert total_cost == 165.75
        assert cost_per_hour == 66.3  # 165.75 / 2.5 h

    def test_compute_stats_cost_per_hour_none_when_no_flight_hours(self, app):
        """The total_hours == 0 guard must yield cost_per_hour is None, not a
        crash and not a bogus 0.0 rate."""
        from expenses.routes import _compute_stats  # pyright: ignore[reportMissingImports]

        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_expense(app, ac_id, amount=50.0, exp_date=date.today())
        with app.app_context():
            expenses = Expense.query.filter_by(aircraft_id=ac_id).all()
            total_cost, cost_per_hour, _label = _compute_stats(expenses, ac_id, 0)
        assert total_cost == 50.0
        assert cost_per_hour is None

    def test_list_aborts_403_for_orphan_user(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        with app.app_context():
            orphan = User(
                email="orphan@example.com",
                password_hash=_pw_hash.hash("x"),
                is_active=True,
            )
            db.session.add(orphan)
            db.session.commit()
            oid = orphan.id
        with client.session_transaction() as sess:
            sess["user_id"] = oid
        resp = client.get(f"/aircraft/{ac_id}/expenses")
        assert resp.status_code == 403

    def test_list_requires_login(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        resp = client.get(f"/aircraft/{ac_id}/expenses")
        assert resp.status_code == 302

    def test_list_404_wrong_tenant(self, client, app):
        _, t1 = _create_user_and_tenant(app, "owner@example.com")
        _, t2 = _create_user_and_tenant(app, "other@example.com")
        ac_id = _add_aircraft(app, t2)
        _login(app, client, "owner@example.com")
        resp = client.get(f"/aircraft/{ac_id}/expenses")
        assert resp.status_code == 404

    def test_invalid_period_falls_back_to_default(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/expenses?period=not-a-number")
        assert resp.status_code == 200


# ── Add expense ───────────────────────────────────────────────────────────────


class TestAddExpense:
    def _post(self, client, ac_id, data):
        return client.post(
            f"/aircraft/{ac_id}/expenses/add", data=data, follow_redirects=True
        )

    def test_get_form_renders(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/expenses/add")
        assert resp.status_code == 200

    def test_post_creates_expense(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        self._post(
            client,
            ac_id,
            {
                "date": "2025-03-01",
                "expense_type": "fuel",
                "amount": "120.00",
                "currency": "EUR",
                "quantity": "40.0",
                "unit": "L",
            },
        )
        with app.app_context():
            exp = Expense.query.filter_by(aircraft_id=ac_id).first()
            assert exp is not None
            assert float(exp.amount) == 120.0
            assert float(exp.quantity) == 40.0

    def test_post_missing_date_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = self._post(
            client,
            ac_id,
            {
                "expense_type": "fuel",
                "amount": "50.00",
                "currency": "EUR",
            },
        )
        assert b"Date is required" in resp.data

    def test_post_missing_amount_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = self._post(
            client,
            ac_id,
            {
                "date": "2025-03-01",
                "expense_type": "fuel",
                "currency": "EUR",
            },
        )
        assert b"Amount is required" in resp.data

    def test_post_invalid_expense_type_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = self._post(
            client,
            ac_id,
            {
                "date": "2025-03-01",
                "expense_type": "invalid",
                "amount": "50.00",
                "currency": "EUR",
            },
        )
        assert b"Invalid expense type" in resp.data

    def test_post_negative_amount_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = self._post(
            client,
            ac_id,
            {
                "date": "2025-03-01",
                "expense_type": "fuel",
                "amount": "-10.00",
                "currency": "EUR",
            },
        )
        assert b"non-negative" in resp.data

    def test_post_negative_quantity_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = self._post(
            client,
            ac_id,
            {
                "date": "2025-03-01",
                "expense_type": "fuel",
                "amount": "50.00",
                "currency": "EUR",
                "quantity": "-5",
            },
        )
        assert b"non-negative" in resp.data

    def test_post_invalid_date_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = self._post(
            client,
            ac_id,
            {
                "date": "not-a-date",
                "expense_type": "fuel",
                "amount": "50.00",
                "currency": "EUR",
            },
        )
        assert b"Invalid date format" in resp.data

    def test_add_requires_login(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        resp = client.get(f"/aircraft/{ac_id}/expenses/add")
        assert resp.status_code == 302


# ── Expense category & coverage period (Phase 36) ─────────────────────────────


class TestExpenseCategoryAndCoverage:
    def _post(self, client, ac_id, data):
        return client.post(
            f"/aircraft/{ac_id}/expenses/add", data=data, follow_redirects=True
        )

    def test_explicit_category_overrides_type_default(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app, "cat1@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client, "cat1@example.com")
        self._post(
            client,
            ac_id,
            {
                "date": "2025-03-01",
                "expense_type": "fuel",
                "expense_category": "fixed",
                "amount": "50.00",
                "currency": "EUR",
            },
        )
        with app.app_context():
            exp = Expense.query.filter_by(aircraft_id=ac_id).first()
            assert exp.expense_category == ExpenseCategory.FIXED

    def test_blank_category_defaults_by_type(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app, "cat2@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client, "cat2@example.com")
        self._post(
            client,
            ac_id,
            {
                "date": "2025-03-01",
                "expense_type": "insurance",
                "amount": "600.00",
                "currency": "EUR",
            },
        )
        with app.app_context():
            exp = Expense.query.filter_by(aircraft_id=ac_id).first()
            assert exp.expense_category == ExpenseCategory.FIXED

    def test_invalid_category_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app, "cat3@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client, "cat3@example.com")
        resp = self._post(
            client,
            ac_id,
            {
                "date": "2025-03-01",
                "expense_type": "fuel",
                "expense_category": "bogus",
                "amount": "50.00",
                "currency": "EUR",
            },
        )
        assert b"Invalid expense category" in resp.data

    def test_coverage_dates_saved(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app, "cov1@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client, "cov1@example.com")
        self._post(
            client,
            ac_id,
            {
                "date": "2025-01-01",
                "expense_type": "insurance",
                "expense_category": "fixed",
                "amount": "1200.00",
                "currency": "EUR",
                "coverage_start": "2025-01-01",
                "coverage_end": "2025-12-31",
            },
        )
        with app.app_context():
            exp = Expense.query.filter_by(aircraft_id=ac_id).first()
            assert exp.coverage_start == date(2025, 1, 1)
            assert exp.coverage_end == date(2025, 12, 31)

    def test_coverage_only_start_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app, "cov2@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client, "cov2@example.com")
        resp = self._post(
            client,
            ac_id,
            {
                "date": "2025-01-01",
                "expense_type": "insurance",
                "expense_category": "fixed",
                "amount": "1200.00",
                "currency": "EUR",
                "coverage_start": "2025-01-01",
            },
        )
        assert b"must both be set" in resp.data

    def test_coverage_invalid_date_format_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app, "cov3@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client, "cov3@example.com")
        resp = self._post(
            client,
            ac_id,
            {
                "date": "2025-01-01",
                "expense_type": "insurance",
                "expense_category": "fixed",
                "amount": "1200.00",
                "currency": "EUR",
                "coverage_start": "not-a-date",
                "coverage_end": "2025-12-31",
            },
        )
        assert b"Invalid coverage date format" in resp.data

    def test_coverage_end_before_start_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app, "cov4@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client, "cov4@example.com")
        resp = self._post(
            client,
            ac_id,
            {
                "date": "2025-01-01",
                "expense_type": "insurance",
                "expense_category": "fixed",
                "amount": "1200.00",
                "currency": "EUR",
                "coverage_start": "2025-12-31",
                "coverage_end": "2025-01-01",
            },
        )
        assert b"must not be before" in resp.data


# ── Edit expense ──────────────────────────────────────────────────────────────


class TestEditExpense:
    def test_get_form_renders(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        exp_id = _add_expense(app, ac_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/expenses/{exp_id}/edit")
        assert resp.status_code == 200

    def test_post_updates_expense(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        exp_id = _add_expense(app, ac_id, amount=100.0)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/expenses/{exp_id}/edit",
            data={
                "date": "2025-04-01",
                "expense_type": "parts",
                "amount": "350.00",
                "currency": "EUR",
            },
            follow_redirects=True,
        )
        with app.app_context():
            exp = db.session.get(Expense, exp_id)
            assert float(exp.amount) == 350.0
            assert exp.expense_type == ExpenseType.PARTS

    def test_post_validation_error_rerenders_form(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        exp_id = _add_expense(app, ac_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/expenses/{exp_id}/edit",
            data={
                "date": "",
                "expense_type": "fuel",
                "amount": "50",
                "currency": "EUR",
            },
            follow_redirects=True,
        )
        assert b"Date is required" in resp.data

    def test_edit_404_wrong_aircraft(self, client, app):
        _, t1 = _create_user_and_tenant(app, "a@example.com")
        _, t2 = _create_user_and_tenant(app, "b@example.com")
        ac1 = _add_aircraft(app, t1)
        ac2 = _add_aircraft(app, t2)
        exp_id = _add_expense(app, ac2)
        _login(app, client, "a@example.com")
        resp = client.get(f"/aircraft/{ac1}/expenses/{exp_id}/edit")
        assert resp.status_code == 404


# ── Delete expense ────────────────────────────────────────────────────────────


class TestDeleteExpense:
    def test_delete_removes_expense(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        exp_id = _add_expense(app, ac_id)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/expenses/{exp_id}/delete", follow_redirects=True
        )
        with app.app_context():
            assert db.session.get(Expense, exp_id) is None

    def test_delete_404_wrong_aircraft(self, client, app):
        _, t1 = _create_user_and_tenant(app, "x@example.com")
        _, t2 = _create_user_and_tenant(app, "y@example.com")
        ac1 = _add_aircraft(app, t1)
        ac2 = _add_aircraft(app, t2)
        exp_id = _add_expense(app, ac2)
        _login(app, client, "x@example.com")
        resp = client.post(f"/aircraft/{ac1}/expenses/{exp_id}/delete")
        assert resp.status_code == 404


# ── Fuel cost on flight form ──────────────────────────────────────────────────


class TestFuelOnFlightForm:
    def _post_flight(self, client, ac_id, extra=None):
        data = {
            "aircraft_id": str(ac_id),
            "date": "2025-06-01",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.5",
            "crew_name_0": "Test Pilot",
            "crew_role_0": "PIC",
        }
        if extra:
            data.update(extra)
        return client.post("/flights/new", data=data, follow_redirects=True)

    def test_flight_with_fuel_before_saves_fuel_fields(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        self._post_flight(
            client,
            ac_id,
            {
                "fuel_event": "before",
                "fuel_added_qty": "45.0",
                "fuel_added_unit": "L",
                "fuel_remaining_qty": "30.0",
            },
        )
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=ac_id).first()
            assert fe is not None
            assert fe.fuel_event == "before"
            assert float(fe.fuel_added_qty) == 45.0
            assert fe.fuel_added_unit == "L"
            assert float(fe.fuel_remaining_qty) == 30.0

    def test_flight_without_fuel_creates_no_expense(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        self._post_flight(client, ac_id)
        with app.app_context():
            assert Expense.query.filter_by(aircraft_id=ac_id).count() == 0

    def test_flight_fuel_negative_remaining_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = self._post_flight(client, ac_id, {"fuel_remaining_qty": "-10"})
        assert b"non-negative" in resp.data

    def test_flight_fuel_negative_quantity_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = self._post_flight(
            client,
            ac_id,
            {
                "fuel_event": "before",
                "fuel_added_qty": "-10",
            },
        )
        assert b"non-negative" in resp.data

    def test_edit_flight_shows_existing_fuel(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        flight_id = _add_flight(app, ac_id)
        with app.app_context():
            fe = db.session.get(FlightEntry, flight_id)
            fe.fuel_event = "before"
            fe.fuel_added_qty = 45.0
            fe.fuel_added_unit = "L"
            fe.fuel_remaining_qty = 30.0
            db.session.commit()
        _login(app, client)
        resp = client.get(f"/flights/{flight_id}/edit")
        assert b"45.0" in resp.data

    def test_edit_flight_clears_fuel_when_none_selected(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        flight_id = _add_flight(app, ac_id, hobbs_start=200.0, hobbs_end=201.5)
        with app.app_context():
            fe = db.session.get(FlightEntry, flight_id)
            fe.fuel_event = "before"
            fe.fuel_added_qty = 45.0
            fe.fuel_added_unit = "L"
            db.session.commit()
        _login(app, client)
        client.post(
            f"/flights/{flight_id}/edit",
            data={
                "date": "2025-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "200.0",
                "flight_time_counter_end": "201.5",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
                "fuel_event": "none",
            },
            follow_redirects=True,
        )
        with app.app_context():
            fe = db.session.get(FlightEntry, flight_id)
            assert fe.fuel_event is None
            assert fe.fuel_added_qty is None
