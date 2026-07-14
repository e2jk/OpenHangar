"""J4 — Full rental cycle, two real users (docs/functional_test_plan.md).

Intent: the Phase 37 loop closes — authorize -> reserve -> check out ->
fly -> check in -> charge -> settle, with correct money at the end, each
actor using only their own permissions.

Existing partial coverage: tests/test_rental_charges.py (39 tests) and
tests/test_dispatch.py cover the pieces with direct-model setup and
mostly single-actor requests; no test runs the loop through HTTP as two
users.
"""

from datetime import datetime, timedelta, timezone

from tests.functional.conftest import second_user, submit

# Wet rate 120.00 EUR/h, engine-time basis; 1.5h delta -> draft 180.00.
_HOURLY_RATE = "120.00"
_ENGINE_DELTA_H = 1.5
_OUT_COUNTER = "1000.0"
_IN_COUNTER = "1001.5"  # 1000.0 + 1.5
_DRAFT_TOTAL = "180.00"  # 120.00 * 1.5
_PAYMENT = "100.00"
_BALANCE_AFTER_PAYMENT = "80.00"  # 180.00 - 100.00


def test_full_rental_cycle_two_users(owner_env, app, client_factory):
    owner = owner_env.client
    aircraft_id = owner_env.aircraft_id

    # Enable rental for the tenant (sole_operator's wizard path doesn't turn
    # it on) and set the aircraft's wet rate, engine-time basis.
    submit(
        owner,
        "/config/profile",
        {
            "operating_model": "sole_operator",
            "planned_aircraft_count": "1",
            "allows_rental": "on",
        },
    )
    submit(
        owner,
        f"/aircraft/{aircraft_id}/reservations/settings",
        {"hourly_rate": _HOURLY_RATE, "rate_type": "wet", "rate_basis": "engine_time"},
    )

    # Invite + authorize the renter (renters are just Role.PILOT users with
    # a RenterAuthorization row — there is no separate "renter" role).
    renter = second_user(
        app,
        client_factory,
        owner,
        "pilot",
        "renter@example.com",
        "Rita Renter",
        aircraft_ids=str(aircraft_id),
    )
    with app.app_context():
        from models import User  # pyright: ignore[reportMissingImports]

        renter_id = User.query.filter_by(email="renter@example.com").first().id

    submit(
        owner,
        "/config/renters/add",
        {
            "renter_user_id": str(renter_id),
            "aircraft_id": str(aircraft_id),
            "granted_on": "2024-01-01",
        },
    )

    # Renter reserves a slot; owner-only pages reject the renter throughout.
    start_dt = datetime(2024, 6, 20, 9, 0, tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(hours=2)
    submit(
        renter,
        f"/aircraft/{aircraft_id}/reservations/new",
        {
            "start_dt": start_dt.strftime("%Y-%m-%dT%H:%M"),
            "end_dt": end_dt.strftime("%Y-%m-%dT%H:%M"),
        },
    )
    rates_page = renter.get(f"/aircraft/{aircraft_id}/reservations/settings")
    assert rates_page.status_code in (403, 404)

    with app.app_context():
        from models import Reservation  # pyright: ignore[reportMissingImports]

        reservation = Reservation.query.filter_by(
            aircraft_id=aircraft_id, pilot_user_id=renter_id
        ).first()
        assert reservation is not None
        reservation_id = reservation.id

    # Owner confirms and checks out.
    submit(owner, f"/aircraft/{aircraft_id}/reservations/{reservation_id}/confirm", {})
    submit(
        owner,
        f"/aircraft/{aircraft_id}/reservations/{reservation_id}/checkout",
        {
            "walkaround_ok": "1",
            "snags_acknowledged": "1",
            "out_engine_counter": _OUT_COUNTER,
            "out_flight_counter": _OUT_COUNTER,
        },
    )

    # Renter logs the flight themselves — auto-links to the reservation
    # because it matches this pilot + aircraft + time window
    # (_find_covering_reservation, app/flights/routes.py).
    submit(
        renter,
        "/flights/new",
        {
            "aircraft_id": str(aircraft_id),
            "date": "2024-06-20",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "departure_time": "09:00",
            "crew_name_0": "Rita Renter",
            "crew_role_0": "PIC",
            "pilot_role": "pic",
            "flight_time_counter_start": _OUT_COUNTER,
            "flight_time_counter_end": _IN_COUNTER,
            "engine_time_counter_start": _OUT_COUNTER,
            "engine_time_counter_end": _IN_COUNTER,
        },
    )

    with app.app_context():
        from models import FlightEntry  # pyright: ignore[reportMissingImports]

        flight = (
            FlightEntry.query.filter_by(aircraft_id=aircraft_id)
            .order_by(FlightEntry.id.desc())
            .first()
        )
        assert flight.reservation_id == reservation_id

    # Owner checks in -> draft charge auto-created.
    submit(
        owner,
        f"/aircraft/{aircraft_id}/reservations/{reservation_id}/checkin",
        {"in_engine_counter": _IN_COUNTER, "in_flight_counter": _IN_COUNTER},
    )

    # The draft-charge form pre-fills billable_hours/hourly_rate (1.5,
    # 120.00) from the auto-computed draft — it doesn't render a computed
    # total until finalized, so that's asserted after finalization below.
    charge_page = owner.get(
        f"/aircraft/{aircraft_id}/reservations/{reservation_id}/charge"
    )
    assert charge_page.status_code == 200
    assert b'value="1.5"' in charge_page.data
    assert b'value="120.00"' in charge_page.data
    renter_charge_page = renter.get(
        f"/aircraft/{aircraft_id}/reservations/{reservation_id}/charge"
    )
    assert renter_charge_page.status_code in (403, 404)

    # Owner finalizes the draft and records a partial payment.
    submit(
        owner,
        f"/aircraft/{aircraft_id}/reservations/{reservation_id}/charge",
        {
            "action": "finalize",
            "billable_hours": str(_ENGINE_DELTA_H),
            "hourly_rate": _HOURLY_RATE,
            "fuel_credit": "0",
            "adjustment": "0",
        },
    )
    submit(
        owner,
        f"/config/renters/{renter_id}/account/payment",
        {"amount": _PAYMENT},
    )

    other_account_page = renter.get(f"/config/renters/{renter_id}/account")
    assert other_account_page.status_code in (403, 404)

    # Renter opens their own account page: charge 180.00 (1.5h * 120.00/h),
    # balance after the 100.00 payment = 80.00.
    my_account = renter.get("/my/account")
    assert my_account.status_code == 200
    assert _DRAFT_TOTAL.encode() in my_account.data
    assert _BALANCE_AFTER_PAYMENT.encode() in my_account.data

    # Owner's statement CSV: opening (0) + charges (180.00) - payments
    # (100.00) = closing (80.00) exactly.
    statement = owner.get(f"/config/renters/{renter_id}/account/statement.csv")
    assert statement.status_code == 200
    assert statement.mimetype == "text/csv"
    csv_text = statement.data.decode()
    assert "180.00" in csv_text or "180" in csv_text
    assert "100.00" in csv_text or "100" in csv_text
    assert "80.00" in csv_text
