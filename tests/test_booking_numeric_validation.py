"""Numeric trust boundaries exercised through real reservation POSTs."""

from decimal import Decimal

import pytest
from models import (
    AircraftBookingSettings,
    DispatchRecord,
    LedgerEntry,
    RentalCharge,
    Role,
    db,
)

from tests.test_rental_charges import (
    _checkin_cycle,
    _login,
    _make_aircraft,
    _make_reservation,
    _make_user,
)


@pytest.fixture
def booking(app, client):
    uid, tid = _make_user(app, "numeric@example.com", role=Role.OWNER)
    acid = _make_aircraft(app, tid)
    rid = _make_reservation(app, acid, uid)
    _login(app, client, uid)
    return acid, rid


BAD = ["nan", "NaN", "inf", "-inf", "1e999999", "999999999999", "-1", "oops"]


@pytest.mark.parametrize("value", BAD)
@pytest.mark.parametrize(
    "field",
    ["min_booking_hours", "max_booking_hours", "hourly_rate", "min_hours_per_day"],
)
def test_settings_reject_invalid_without_mutation(
    app, client, booking, field: str, value: str
) -> None:
    acid, _ = booking
    with app.app_context():
        db.session.add(
            AircraftBookingSettings(aircraft_id=acid, hourly_rate=Decimal("42.00"))
        )
        db.session.commit()
    response = client.post(
        f"/aircraft/{acid}/reservations/settings", data={field: value}
    )
    assert response.status_code == 200
    with app.app_context():
        settings = AircraftBookingSettings.query.filter_by(aircraft_id=acid).one()
        assert settings.hourly_rate == Decimal("42.00")
        assert settings.min_booking_hours is None
        assert settings.max_booking_hours is None
        assert settings.min_hours_per_day is None


@pytest.mark.parametrize("value", BAD)
@pytest.mark.parametrize("kind", ["engine", "flight"])
@pytest.mark.parametrize("stage", ["checkout", "checkin"])
def test_dispatch_rejects_invalid_without_mutation(
    app, client, booking, value: str, kind: str, stage: str
) -> None:
    acid, rid = booking
    base = f"/aircraft/{acid}/reservations/{rid}"
    if stage == "checkin":
        assert (
            client.post(
                base + "/checkout",
                data={"walkaround_ok": "1", "snags_acknowledged": "1"},
            ).status_code
            == 302
        )
    prefix = "out" if stage == "checkout" else "in"
    response = client.post(
        base + "/" + stage,
        data={
            f"{prefix}_{kind}_counter": value,
            "walkaround_ok": "1",
            "snags_acknowledged": "1",
        },
    )
    assert response.status_code == 200
    assert b"Invalid" in response.data
    with app.app_context():
        dispatch = DispatchRecord.query.filter_by(reservation_id=rid).first()
        if stage == "checkout":
            assert dispatch is None
        else:
            assert dispatch.in_at is None
            assert dispatch.in_engine_counter is None
            assert dispatch.in_flight_counter is None
        assert RentalCharge.query.count() == 0


@pytest.mark.parametrize("value", BAD)
@pytest.mark.parametrize(
    "field", ["billable_hours", "hourly_rate", "fuel_credit", "adjustment"]
)
def test_charge_rejects_invalid_without_mutation(
    app, client, booking, value: str, field: str
) -> None:
    if field == "adjustment" and value == "-1":
        return  # Signed adjustments are covered by the successful calculation below.
    acid, rid = booking
    _checkin_cycle(app, client, acid, rid)
    data = {
        "billable_hours": "2",
        "hourly_rate": "12",
        "fuel_credit": "0",
        "adjustment": "0",
        "adjustment_note": "correction",
        "action": "finalize",
    }
    data[field] = value
    with app.app_context():
        charge = RentalCharge.query.one()
        before = (
            charge.billable_hours,
            charge.hourly_rate,
            charge.fuel_credit,
            charge.adjustment,
            charge.total,
            charge.status,
        )
    response = client.post(f"/aircraft/{acid}/reservations/{rid}/charge", data=data)
    assert response.status_code == 200
    with app.app_context():
        charge = RentalCharge.query.one()
        assert (
            charge.billable_hours,
            charge.hourly_rate,
            charge.fuel_credit,
            charge.adjustment,
            charge.total,
            charge.status,
        ) == before
        assert LedgerEntry.query.count() == 0


def test_charge_decimal_rounding_and_negative_adjustment(app, client, booking) -> None:
    acid, rid = booking
    _checkin_cycle(app, client, acid, rid)
    response = client.post(
        f"/aircraft/{acid}/reservations/{rid}/charge",
        data={
            "billable_hours": "1.5",
            "hourly_rate": "2.35",
            "fuel_credit": "0.01",
            "adjustment": "-0.01",
            "adjustment_note": "correction",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        charge = RentalCharge.query.one()
        # 1.5 * 2.35 - .01 - .01 = 3.505; ties round to even cents.
        assert charge.total == Decimal("3.50")
        assert charge.adjustment == Decimal("-0.01")


def test_charge_product_overflow_rejected(app, client, booking) -> None:
    acid, rid = booking
    _checkin_cycle(app, client, acid, rid)
    response = client.post(
        f"/aircraft/{acid}/reservations/{rid}/charge",
        data={"billable_hours": "99999.9", "hourly_rate": "999999.99"},
    )
    assert response.status_code == 200
    with app.app_context():
        assert RentalCharge.query.one().billable_hours == Decimal("2.0")


@pytest.mark.parametrize("counter,rate", [("100000", "1"), ("10000", "999999")])
def test_draft_overflow_leaves_dispatch_open(
    app, client, booking, counter: str, rate: str
) -> None:
    acid, rid = booking
    with app.app_context():
        db.session.add(
            AircraftBookingSettings(aircraft_id=acid, hourly_rate=Decimal(rate))
        )
        db.session.commit()
    base = f"/aircraft/{acid}/reservations/{rid}"
    client.post(
        base + "/checkout",
        data={
            "out_engine_counter": "0",
            "walkaround_ok": "1",
            "snags_acknowledged": "1",
        },
    )
    response = client.post(base + "/checkin", data={"in_engine_counter": counter})
    assert response.status_code == 200
    with app.app_context():
        assert DispatchRecord.query.one().in_at is None
        assert RentalCharge.query.count() == 0


def test_automatic_draft_uses_decimal_cents(app, client, booking) -> None:
    acid, rid = booking
    with app.app_context():
        db.session.add(
            AircraftBookingSettings(aircraft_id=acid, hourly_rate=Decimal("2.35"))
        )
        db.session.commit()
    _checkin_cycle(app, client, acid, rid, out_engine=0, in_engine=1.5)
    with app.app_context():
        charge = RentalCharge.query.one()
        # 1.5 * 2.35 = 3.525, rounded to even cents independently of binary floats.
        assert charge.billable_hours == Decimal("1.5")
        assert charge.hourly_rate == Decimal("2.35")
        assert charge.total == Decimal("3.52")


@pytest.mark.parametrize(
    "field", ["min_booking_hours", "max_booking_hours", "min_hours_per_day"]
)
def test_zero_booking_duration_rejected(app, client, booking, field: str) -> None:
    acid, _ = booking
    response = client.post(f"/aircraft/{acid}/reservations/settings", data={field: "0"})
    assert response.status_code == 200
    with app.app_context():
        assert AircraftBookingSettings.query.count() == 0


def test_blank_optional_charge_values_are_zero(app, client, booking) -> None:
    acid, rid = booking
    _checkin_cycle(app, client, acid, rid)
    response = client.post(
        f"/aircraft/{acid}/reservations/{rid}/charge",
        data={"billable_hours": "1", "hourly_rate": "12"},
    )
    assert response.status_code == 302
    with app.app_context():
        charge = RentalCharge.query.one()
        assert charge.fuel_credit == 0
        assert charge.adjustment == 0
        assert charge.total == Decimal("12.00")
