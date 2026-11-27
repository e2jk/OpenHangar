from datetime import UTC, date, datetime, timedelta

from expenses import fee_checks
from flask import Flask
from flask.testing import FlaskClient
from models import (
    DataCheckDismissal,
    Expense,
    ExpenseType,
    Flight,
    Role,
    TenantUser,
    db,
)

from tests.test_expenses import (
    _add_aircraft,
    _add_expense,
    _add_flight,
    _create_user_and_tenant,
    _login,
)

REVIEW = "/expenses/fee-checks"


def test_review_airfield_fees(app: Flask, client: FlaskClient) -> None:
    user_id, tenant_id = _create_user_and_tenant(app)
    aircraft_id = _add_aircraft(app, tenant_id)
    today = date.today()
    for days in (50, 40, 30):
        flown = today - timedelta(days=days)
        flight_id = _add_flight(app, aircraft_id, flight_date=flown)
        _add_expense(
            app,
            aircraft_id,
            expense_type=ExpenseType.AIRFIELD_FEES,
            exp_date=flown,
            flight_entry_id=flight_id,
        )
    missing_on = today - timedelta(days=15)
    missing = _add_flight(app, aircraft_id, flight_date=missing_on)
    later = _add_flight(app, aircraft_id, flight_date=today - timedelta(days=10))
    _add_flight(app, aircraft_id, flight_date=today)
    legacy = _add_expense(
        app,
        aircraft_id,
        ExpenseType.OTHER,
        exp_date=missing_on,
        description="Taxe d'atterrissage: EBBR",
    )
    unrelated = _add_expense(app, aircraft_id, exp_date=missing_on)
    linked_already = _add_expense(
        app,
        aircraft_id,
        ExpenseType.LANDING_FEE,
        exp_date=missing_on,
        flight_entry_id=later,
    )

    assert client.get(REVIEW).status_code == 302
    _login(app, client)
    response = client.get(REVIEW)
    assert response.status_code == 200
    assert "Landing fee · EBBR" in response.text
    assert "Takeoff fee · EBOS" in response.text
    assert "3 of 3 preceding movements" in response.text
    assert "Taxe d&#39;atterrissage" in response.text
    filtered = client.get(
        f"{REVIEW}?aircraft={aircraft_id}&kind=landing_fee&airfield=ebbr"
        f"&start={missing_on}&end={missing_on}"
    )
    assert "Landing fee · EBBR" in filtered.text
    assert "Takeoff fee · EBOS" not in filtered.text
    assert "No likely missing fees" in client.get(f"{REVIEW}?airfield=EGLL").text
    assert client.get(f"{REVIEW}?aircraft={aircraft_id + 999}").status_code == 404
    assert client.post(REVIEW).status_code == 404
    data = {"flight": missing, "kind": fee_checks.LANDING}
    assert client.post(REVIEW, data={**data, "action": "unknown"}).status_code == 400
    for expense in (None, unrelated):
        fields = {} if expense is None else {"expense": expense}
        assert (
            client.post(REVIEW, data={**data, **fields, "action": "link"}).status_code
            == 400
        )
    assert (
        client.post(
            REVIEW, data={**data, "expense": linked_already, "action": "link"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            REVIEW, data={**data, "expense": legacy, "action": "link"}
        ).status_code
        == 302
    )
    with app.app_context():
        assert db.session.get(Expense, legacy).flight_entry_id == missing

    takeoff_data = {"flight": missing, "kind": fee_checks.TAKEOFF}
    assert (
        client.post(REVIEW, data={**takeoff_data, "action": "defer"}).status_code == 302
    )
    with app.app_context():
        assert [
            row.flight.id
            for row in fee_checks.missing_fees([aircraft_id], missing_on, today, today)
        ] == [later]
        decision = DataCheckDismissal.query.one()
        decision.created_at = datetime.now(UTC) - timedelta(days=31)
        db.session.commit()
    assert (
        client.post(REVIEW, data={**takeoff_data, "action": "defer"}).status_code == 302
    )
    with app.app_context():
        decision = DataCheckDismissal.query.one()
        decision.created_at = datetime.now(UTC) - timedelta(days=31)
        db.session.commit()
    assert (
        client.post(REVIEW, data={**takeoff_data, "action": "no_fee"}).status_code
        == 302
    )
    assert (
        client.post(REVIEW, data={**takeoff_data, "action": "no_fee"}).status_code
        == 302
    )

    url = f"/aircraft/{aircraft_id}/expenses/add?flight_entry_id={later}&fee_kind=takeoff_fee"
    assert 'value="takeoff_fee"' in client.get(url).text
    assert "This expense will be linked to the flight." in client.post(url).text
    assert (
        client.post(
            url,
            data={
                "flight_entry_id": later,
                "date": today.isoformat(),
                "expense_type": fee_checks.TAKEOFF,
                "amount": "12.50",
                "currency": "EUR",
            },
        ).status_code
        == 302
    )
    assert "No likely missing fees" in client.get(REVIEW).text

    with app.app_context():
        membership = TenantUser.query.filter_by(
            user_id=user_id, tenant_id=tenant_id
        ).one()
        membership.role = Role.PILOT
        db.session.commit()
    assert client.get(REVIEW).status_code == 403
    assert (
        client.post(REVIEW, data={**takeoff_data, "action": "no_fee"}).status_code
        == 403
    )


def test_learning_is_directional_and_expires(app: Flask) -> None:
    _, tenant_id = _create_user_and_tenant(app)
    aircraft_id = _add_aircraft(app, tenant_id)
    today = date.today()
    for days in (400, 200, 100, 45):
        flown = today - timedelta(days=days)
        flight_id = _add_flight(app, aircraft_id, flight_date=flown)
        _add_expense(
            app,
            aircraft_id,
            ExpenseType.LANDING_FEE,
            exp_date=flown,
            flight_entry_id=flight_id,
        )
    recent_date = today - timedelta(days=9)
    flight_id = _add_flight(app, aircraft_id, flight_date=recent_date)
    with app.app_context():
        flight = db.session.get(Flight, flight_id)
        flight.departure_icao = ""
        db.session.commit()
        findings = fee_checks.missing_fees(
            [aircraft_id], today - timedelta(days=200), today, today
        )
        assert [(row.flight.id, row.kind, row.charged) for row in findings] == [
            (flight_id, fee_checks.LANDING, 3)
        ]


def test_fee_exemptions_change_the_learned_pattern(app: Flask) -> None:
    _, tenant_id = _create_user_and_tenant(app)
    aircraft_id = _add_aircraft(app, tenant_id)
    today = date.today()
    for days in (60, 50, 40):
        flown = today - timedelta(days=days)
        flight_id = _add_flight(app, aircraft_id, flight_date=flown)
        _add_expense(
            app,
            aircraft_id,
            ExpenseType.LANDING_FEE,
            exp_date=flown,
            flight_entry_id=flight_id,
        )
    exempt_ids = [
        _add_flight(app, aircraft_id, flight_date=today - timedelta(days=days))
        for days in (30, 20)
    ]
    target = _add_flight(app, aircraft_id, flight_date=today - timedelta(days=10))
    with app.app_context():
        start = today - timedelta(days=10)
        assert [
            row.flight.id
            for row in fee_checks.missing_fees([aircraft_id], start, today, today)
        ] == [target]
        key = fee_checks.feedback_key(fee_checks.LANDING, "EBBR", "no_fee")
        fee_checks.record_feedback(exempt_ids[0], key, None)
        db.session.add(
            Expense(
                aircraft_id=aircraft_id,
                flight_entry_id=exempt_ids[1],
                expense_type=ExpenseType.LANDING_FEE,
                date=today - timedelta(days=20),
                amount=0,
            )
        )
        db.session.commit()
        assert not fee_checks.missing_fees([aircraft_id], start, today, today)
