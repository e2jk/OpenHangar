"""
Tests for Phase 8: Cost tracking (Expense model, expenses blueprint, fuel on flight form).
"""
import bcrypt  # pyright: ignore[reportMissingImports]
from datetime import date, timedelta

import pytest  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft, Expense, ExpenseType, FlightEntry,
    Role, Tenant, TenantUser, User, db,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_user_and_tenant(app, email="pilot@example.com"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER))
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
        ac = Aircraft(tenant_id=tenant_id, registration="OO-TST", make="Cessna", model="172S")
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_expense(app, aircraft_id, expense_type=ExpenseType.FUEL,
                 amount=100.0, exp_date=None, currency="EUR",
                 quantity=None, unit=None, description=None):
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
        )
        db.session.add(exp)
        db.session.commit()
        return exp.id


def _add_flight(app, aircraft_id, hobbs_start=100.0, hobbs_end=101.5, flight_date=None):
    with app.app_context():
        fe = FlightEntry(
            aircraft_id=aircraft_id,
            date=flight_date or date.today(),
            departure_icao="EBOS", arrival_icao="EBBR",
            flight_time_counter_start=hobbs_start, flight_time_counter_end=hobbs_end,
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
        _add_expense(app, ac_id, expense_type=ExpenseType.INSURANCE, description="PolicyABC")
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
        _add_flight(app, ac_id, hobbs_start=100.0, hobbs_end=102.0,
                    flight_date=date.today())
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/expenses?period=0")
        assert b"75.00" in resp.data  # 150 / 2 h = 75/h

    def test_list_aborts_403_for_orphan_user(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        with app.app_context():
            orphan = User(email="orphan@example.com",
                          password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
                          is_active=True)
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
        return client.post(f"/aircraft/{ac_id}/expenses/add",
                           data=data, follow_redirects=True)

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
        self._post(client, ac_id, {
            "date": "2025-03-01", "expense_type": "fuel",
            "amount": "120.00", "currency": "EUR",
            "quantity": "40.0", "unit": "L",
        })
        with app.app_context():
            exp = Expense.query.filter_by(aircraft_id=ac_id).first()
            assert exp is not None
            assert float(exp.amount) == 120.0
            assert float(exp.quantity) == 40.0

    def test_post_missing_date_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = self._post(client, ac_id, {
            "expense_type": "fuel", "amount": "50.00", "currency": "EUR",
        })
        assert b"Date is required" in resp.data

    def test_post_missing_amount_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = self._post(client, ac_id, {
            "date": "2025-03-01", "expense_type": "fuel", "currency": "EUR",
        })
        assert b"Amount is required" in resp.data

    def test_post_invalid_expense_type_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = self._post(client, ac_id, {
            "date": "2025-03-01", "expense_type": "invalid",
            "amount": "50.00", "currency": "EUR",
        })
        assert b"Invalid expense type" in resp.data

    def test_post_negative_amount_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = self._post(client, ac_id, {
            "date": "2025-03-01", "expense_type": "fuel",
            "amount": "-10.00", "currency": "EUR",
        })
        assert b"non-negative" in resp.data

    def test_post_negative_quantity_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = self._post(client, ac_id, {
            "date": "2025-03-01", "expense_type": "fuel",
            "amount": "50.00", "currency": "EUR", "quantity": "-5",
        })
        assert b"non-negative" in resp.data

    def test_post_invalid_date_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = self._post(client, ac_id, {
            "date": "not-a-date", "expense_type": "fuel",
            "amount": "50.00", "currency": "EUR",
        })
        assert b"Invalid date format" in resp.data

    def test_add_requires_login(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        resp = client.get(f"/aircraft/{ac_id}/expenses/add")
        assert resp.status_code == 302


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
        client.post(f"/aircraft/{ac_id}/expenses/{exp_id}/edit",
                    data={"date": "2025-04-01", "expense_type": "parts",
                          "amount": "350.00", "currency": "EUR"},
                    follow_redirects=True)
        with app.app_context():
            exp = db.session.get(Expense, exp_id)
            assert float(exp.amount) == 350.0
            assert exp.expense_type == ExpenseType.PARTS

    def test_post_validation_error_rerenders_form(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        exp_id = _add_expense(app, ac_id)
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/expenses/{exp_id}/edit",
                           data={"date": "", "expense_type": "fuel",
                                 "amount": "50", "currency": "EUR"},
                           follow_redirects=True)
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
        client.post(f"/aircraft/{ac_id}/expenses/{exp_id}/delete",
                    follow_redirects=True)
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
            "date": "2025-06-01",
            "departure_icao": "EBOS", "arrival_icao": "EBBR",
            "hobbs_start": "100.0", "hobbs_end": "101.5",
        }
        if extra:
            data.update(extra)
        return client.post(f"/aircraft/{ac_id}/flights/new",
                           data=data, follow_redirects=True)

    def test_flight_with_fuel_creates_expense(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        self._post_flight(client, ac_id, {
            "fuel_cost": "135.00", "fuel_currency": "EUR",
            "fuel_quantity": "45.0", "fuel_unit": "L",
        })
        with app.app_context():
            exp = Expense.query.filter_by(aircraft_id=ac_id).first()
            assert exp is not None
            assert exp.expense_type == ExpenseType.FUEL
            assert float(exp.amount) == 135.0
            assert float(exp.quantity) == 45.0
            assert exp.flight_entry_id is not None

    def test_flight_without_fuel_creates_no_expense(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        self._post_flight(client, ac_id)
        with app.app_context():
            assert Expense.query.filter_by(aircraft_id=ac_id).count() == 0

    def test_flight_fuel_negative_cost_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = self._post_flight(client, ac_id, {"fuel_cost": "-50"})
        assert b"non-negative" in resp.data

    def test_flight_fuel_negative_quantity_shows_error(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = self._post_flight(client, ac_id, {
            "fuel_cost": "50", "fuel_quantity": "-10",
        })
        assert b"non-negative" in resp.data

    def test_edit_flight_shows_existing_fuel(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        flight_id = _add_flight(app, ac_id)
        with app.app_context():
            fe = db.session.get(FlightEntry, flight_id)
            exp = Expense(
                aircraft_id=ac_id, flight_entry_id=flight_id,
                date=fe.date, expense_type=ExpenseType.FUEL,
                amount=99.50, currency="EUR", quantity=32.0, unit="L",
            )
            db.session.add(exp)
            db.session.commit()
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/flights/{flight_id}/edit")
        assert b"99.50" in resp.data or b"99.5" in resp.data

    def test_edit_flight_removes_fuel_when_cleared(self, client, app):
        uid, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        flight_id = _add_flight(app, ac_id, hobbs_start=200.0, hobbs_end=201.5)
        with app.app_context():
            fe = db.session.get(FlightEntry, flight_id)
            exp = Expense(
                aircraft_id=ac_id, flight_entry_id=flight_id,
                date=fe.date, expense_type=ExpenseType.FUEL,
                amount=80.0, currency="EUR",
            )
            db.session.add(exp)
            db.session.commit()
            exp_id = exp.id
        _login(app, client)
        # Post without fuel_cost → should delete the linked expense
        client.post(f"/aircraft/{ac_id}/flights/{flight_id}/edit", data={
            "date": "2025-06-01",
            "departure_icao": "EBOS", "arrival_icao": "EBBR",
            "hobbs_start": "200.0", "hobbs_end": "201.5",
        }, follow_redirects=True)
        with app.app_context():
            assert db.session.get(Expense, exp_id) is None
