"""
Tests for recurring fixed-cost expenses:
  - month arithmetic (end-of-month clamping, index-based dates without drift)
  - the daily materialisation pass (catch-up, cursor, recurrence_end,
    archived aircraft, deleted rows staying deleted)
  - expense form validation and persistence of the recurrence fields
  - list badges for template and generated rows
"""

from datetime import date, datetime, timezone

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (
    Aircraft,
    Expense,
    ExpenseCategory,
    ExpenseType,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)  # pyright: ignore[reportMissingImports]
from services.recurring_expense_service import (  # pyright: ignore[reportMissingImports]
    _add_months,
    materialize_recurring_expenses,
)


def _create_user_and_tenant(app, email="owner@example.com"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email, password_hash=_pw_hash.hash("testpassword123"), is_active=True
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="owner@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def _add_aircraft(app, tenant_id, registration="OO-REC", archived=False):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id,
            registration=registration,
            make="Cessna",
            model="172S",
            archived_at=datetime.now(timezone.utc) if archived else None,
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_template(app, aircraft_id, on, recurrence="monthly", **kwargs):
    with app.app_context():
        exp = Expense(
            aircraft_id=aircraft_id,
            date=on,
            expense_type=kwargs.pop("expense_type", ExpenseType.OTHER),
            expense_category=kwargs.pop("expense_category", ExpenseCategory.FIXED),
            description=kwargs.pop("description", "Hangar rent"),
            amount=kwargs.pop("amount", 250),
            recurrence=recurrence,
            **kwargs,
        )
        db.session.add(exp)
        db.session.commit()
        return exp.id


class TestAddMonths:
    def test_simple_advance(self):
        assert _add_months(date(2026, 1, 15), 1) == date(2026, 2, 15)
        assert _add_months(date(2026, 1, 15), 12) == date(2027, 1, 15)

    def test_end_of_month_clamped(self):
        assert _add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
        assert _add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)  # leap year

    def test_index_based_no_drift(self):
        # Jan 31 + 2 months is Mar 31, not Feb 28 + 1 month = Mar 28
        assert _add_months(date(2026, 1, 31), 2) == date(2026, 3, 31)

    def test_year_rollover(self):
        assert _add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


class TestMaterialisation:
    def test_monthly_catch_up_creates_all_due_rows(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        tpl_id = _add_template(app, acid, date(2026, 1, 31), "monthly")
        with app.app_context():
            created = materialize_recurring_expenses(today=date(2026, 4, 10))
            assert created == 2
            rows = (
                Expense.query.filter_by(recurring_template_id=tpl_id)
                .order_by(Expense.date)
                .all()
            )
            assert [r.date for r in rows] == [
                date(2026, 2, 28),
                date(2026, 3, 31),
            ]
            first = rows[0]
            assert float(first.amount) == 250.0
            assert first.expense_type == ExpenseType.OTHER
            assert first.expense_category == ExpenseCategory.FIXED
            assert first.description == "Hangar rent"
            assert first.recurrence is None  # generated rows are ordinary expenses
            tpl = db.session.get(Expense, tpl_id)
            assert tpl.recurrence_last_date == date(2026, 3, 31)

    def test_second_run_creates_nothing(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_template(app, acid, date(2026, 1, 15), "monthly")
        with app.app_context():
            assert materialize_recurring_expenses(today=date(2026, 3, 20)) == 2
            assert materialize_recurring_expenses(today=date(2026, 3, 20)) == 0

    def test_deleted_generated_row_not_recreated(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        tpl_id = _add_template(app, acid, date(2026, 1, 15), "monthly")
        with app.app_context():
            materialize_recurring_expenses(today=date(2026, 2, 20))
            row = Expense.query.filter_by(recurring_template_id=tpl_id).one()
            db.session.delete(row)
            db.session.commit()
            assert materialize_recurring_expenses(today=date(2026, 2, 20)) == 0
            assert Expense.query.filter_by(recurring_template_id=tpl_id).count() == 0

    def test_recurrence_end_respected(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        tpl_id = _add_template(
            app, acid, date(2026, 1, 15), "monthly", recurrence_end=date(2026, 3, 1)
        )
        with app.app_context():
            created = materialize_recurring_expenses(today=date(2026, 6, 1))
            assert created == 1  # only Feb 15 is on or before the end date
            row = Expense.query.filter_by(recurring_template_id=tpl_id).one()
            assert row.date == date(2026, 2, 15)

    def test_quarterly_and_yearly_periods(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        q_id = _add_template(app, acid, date(2025, 11, 1), "quarterly")
        y_id = _add_template(app, acid, date(2025, 6, 1), "yearly")
        with app.app_context():
            materialize_recurring_expenses(today=date(2026, 7, 1))
            q_dates = [
                r.date for r in Expense.query.filter_by(recurring_template_id=q_id)
            ]
            y_dates = [
                r.date for r in Expense.query.filter_by(recurring_template_id=y_id)
            ]
            assert sorted(q_dates) == [date(2026, 2, 1), date(2026, 5, 1)]
            assert y_dates == [date(2026, 6, 1)]

    def test_coverage_span_advances_per_period(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        tpl_id = _add_template(
            app,
            acid,
            date(2026, 1, 1),
            "monthly",
            coverage_start=date(2026, 1, 1),
            coverage_end=date(2026, 1, 31),
        )
        with app.app_context():
            materialize_recurring_expenses(today=date(2026, 2, 1))
            row = Expense.query.filter_by(recurring_template_id=tpl_id).one()
            assert row.coverage_start == date(2026, 2, 1)
            assert row.coverage_end == date(2026, 2, 28)

    def test_archived_aircraft_skipped(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid, registration="OO-OLD", archived=True)
        tpl_id = _add_template(app, acid, date(2026, 1, 1), "monthly")
        with app.app_context():
            assert materialize_recurring_expenses(today=date(2026, 3, 1)) == 0
            assert Expense.query.filter_by(recurring_template_id=tpl_id).count() == 0

    def test_unknown_recurrence_value_skipped(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_template(app, acid, date(2026, 1, 1), "weekly")  # not supported
        with app.app_context():
            assert materialize_recurring_expenses(today=date(2026, 3, 1)) == 0

    def test_nothing_due_before_first_period(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_template(app, acid, date(2026, 1, 15), "monthly")
        with app.app_context():
            assert materialize_recurring_expenses(today=date(2026, 2, 1)) == 0

    def test_occurrence_due_exactly_today_is_created(self, app):
        """Pin the > boundary on the today cutoff: an occurrence whose date is
        exactly today must be created, not deferred to the next run."""
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        tpl_id = _add_template(app, acid, date(2026, 1, 15), "monthly")
        with app.app_context():
            # Next occurrence after the template date is 2026-02-15 exactly.
            created = materialize_recurring_expenses(today=date(2026, 2, 15))
            assert created == 1
            row = Expense.query.filter_by(recurring_template_id=tpl_id).one()
            assert row.date == date(2026, 2, 15)

    def test_occurrence_exactly_on_recurrence_end_is_created(self, app):
        """Pin the > boundary on recurrence_end: an occurrence landing exactly
        on the end date is the last one still created, not skipped."""
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        tpl_id = _add_template(
            app,
            acid,
            date(2026, 1, 15),
            "monthly",
            recurrence_end=date(2026, 2, 15),  # == the next occurrence, exactly
        )
        with app.app_context():
            created = materialize_recurring_expenses(today=date(2026, 6, 1))
            assert created == 1
            row = Expense.query.filter_by(recurring_template_id=tpl_id).one()
            assert row.date == date(2026, 2, 15)


class TestExpenseFormRecurrence:
    def _form(self, **kwargs):
        data = {
            "date": "2026-01-15",
            "expense_type": "other",
            "expense_category": "fixed",
            "amount": "250.00",
            "currency": "EUR",
        }
        data.update(kwargs)
        return data

    def test_recurrence_saved(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{acid}/expenses/add",
            data=self._form(recurrence="monthly", recurrence_end="2026-12-31"),
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            exp = Expense.query.filter_by(aircraft_id=acid).one()
            assert exp.recurrence == "monthly"
            assert exp.recurrence_end == date(2026, 12, 31)

    def test_invalid_recurrence_rejected(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{acid}/expenses/add", data=self._form(recurrence="weekly")
        )
        assert b"Invalid recurrence." in resp.data
        with app.app_context():
            assert Expense.query.filter_by(aircraft_id=acid).count() == 0

    def test_recurrence_end_requires_recurrence(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{acid}/expenses/add",
            data=self._form(recurrence_end="2026-12-31"),
        )
        assert b"A recurrence end date requires a recurrence." in resp.data

    def test_recurrence_end_before_date_rejected(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{acid}/expenses/add",
            data=self._form(recurrence="monthly", recurrence_end="2025-12-31"),
        )
        assert (
            b"The recurrence end date must not be before the expense date." in resp.data
        )

    def test_invalid_recurrence_end_format_rejected(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{acid}/expenses/add",
            data=self._form(recurrence="monthly", recurrence_end="not-a-date"),
        )
        assert b"Invalid recurrence end date format." in resp.data

    def test_clearing_recurrence_resets_cursor(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        tpl_id = _add_template(app, acid, date(2026, 1, 15), "monthly")
        with app.app_context():
            materialize_recurring_expenses(today=date(2026, 2, 20))
            assert db.session.get(Expense, tpl_id).recurrence_last_date is not None
        _login(app, client)
        resp = client.post(
            f"/aircraft/{acid}/expenses/{tpl_id}/edit",
            data=self._form(recurrence=""),
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            tpl = db.session.get(Expense, tpl_id)
            assert tpl.recurrence is None
            assert tpl.recurrence_last_date is None

    def test_list_shows_template_and_generated_badges(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        tpl_id = _add_template(app, acid, date(2026, 1, 15), "monthly")
        with app.app_context():
            materialize_recurring_expenses(today=date(2026, 2, 20))
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/expenses?period=0")
        assert resp.status_code == 200
        assert b"bi-arrow-repeat" in resp.data
        assert f"/expenses/{tpl_id}/edit".encode() in resp.data
