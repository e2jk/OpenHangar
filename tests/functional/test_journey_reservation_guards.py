"""J5 — Reservation guards interplay (docs/functional_test_plan.md).

Intent: the calendar tells the truth — conflicts, downtime, and grounded
aircraft actually prevent/warn what they should, and resolution frees
things up.

Deviations from the plan (documented per its own "deviate only with a
documented reason" rule, both discovered while writing this journey):

1. Overlap and maintenance-downtime conflicts are enforced only at
   *confirm* time (`_has_conflict`, called from `confirm_reservation`,
   app/reservations/routes.py:748), never at creation — a brand-new
   reservation request that overlaps another *pending* reservation, or
   falls inside a downtime window, is accepted (PENDING) regardless; the
   rejection only happens when an owner tries to confirm it. This
   journey therefore books-then-confirms before checking the overlap/
   downtime rejections, rather than expecting them at creation. The
   grounding-snag guard is the exception — that one *is* checked at
   creation (app/reservations/routes.py:985-1020), so it's exercised
   as originally specced.
2. Flipping `TenantProfile.grounded_reservation_policy` to "block" has no
   HTTP route anywhere in the app (confirmed by grep — only ever set via
   direct model write in the existing test suite too), so this journey
   sets it directly with a one-line comment, per the plan's own
   sanctioned exception for "things the UI cannot create".

Existing partial coverage: tests/test_availability_guards.py covers each
guard singly, direct-model; the policy-flip and resolve-then-retry
sequences are new.
"""

from datetime import datetime, timedelta, timezone

from tests.functional.conftest import second_user, submit


def _fmt(dt):
    return dt.strftime("%Y-%m-%dT%H:%M")


def test_reservation_guards_interplay(owner_env, app, client_factory):
    owner = owner_env.client
    aircraft_id = owner_env.aircraft_id

    submit(
        owner,
        "/config/profile",
        {
            "operating_model": "sole_operator",
            "planned_aircraft_count": "1",
            "allows_rental": "on",
            "rental_authorization_policy": "off",
        },
    )

    user_a = second_user(
        app,
        client_factory,
        owner,
        "pilot",
        "a@example.com",
        "User A",
        aircraft_ids=str(aircraft_id),
    )
    user_b = second_user(
        app,
        client_factory,
        owner,
        "pilot",
        "b@example.com",
        "User B",
        aircraft_ids=str(aircraft_id),
    )

    slot_start = datetime(2024, 7, 1, 9, 0, tzinfo=timezone.utc)
    slot_end = slot_start + timedelta(hours=2)

    # User A reserves the slot; owner confirms it.
    submit(
        user_a,
        f"/aircraft/{aircraft_id}/reservations/new",
        {"start_dt": _fmt(slot_start), "end_dt": _fmt(slot_end)},
    )
    with app.app_context():
        from models import Reservation  # pyright: ignore[reportMissingImports]

        res_a = (
            Reservation.query.filter_by(aircraft_id=aircraft_id, start_dt=slot_start)
            .order_by(Reservation.id.desc())
            .first()
        )
        res_a_id = res_a.id
        res_a_pilot_id = res_a.pilot_user_id

    submit(owner, f"/aircraft/{aircraft_id}/reservations/{res_a_id}/confirm", {})

    # User B's overlapping request is accepted (pending) — the guard only
    # fires when the owner tries to confirm it.
    overlap_start = slot_start + timedelta(hours=1)
    overlap_end = overlap_start + timedelta(hours=2)
    submit(
        user_b,
        f"/aircraft/{aircraft_id}/reservations/new",
        {"start_dt": _fmt(overlap_start), "end_dt": _fmt(overlap_end)},
    )
    with app.app_context():
        from models import Reservation  # pyright: ignore[reportMissingImports]

        res_b_overlap = (
            Reservation.query.filter_by(aircraft_id=aircraft_id, start_dt=overlap_start)
            .order_by(Reservation.id.desc())
            .first()
        )
        res_b_overlap_id = res_b_overlap.id

    confirm_resp = submit(
        owner,
        f"/aircraft/{aircraft_id}/reservations/{res_b_overlap_id}/confirm",
        {},
        expect_error=True,
    )
    assert b"Cannot confirm: overlaps" in confirm_resp.data

    # Owner adds a maintenance downtime window; a reservation *inside* it
    # is likewise accepted at creation, then rejected at confirm time.
    downtime_start = datetime(2024, 7, 2, 9, 0, tzinfo=timezone.utc)
    downtime_end = downtime_start + timedelta(hours=8)
    submit(
        owner,
        f"/aircraft/{aircraft_id}/downtimes/new",
        {
            "start_dt": _fmt(downtime_start),
            "end_dt": _fmt(downtime_end),
            "reason": "Annual inspection",
        },
    )
    downtime_res_start = downtime_start + timedelta(hours=1)
    downtime_res_end = downtime_start + timedelta(hours=2)
    submit(
        user_b,
        f"/aircraft/{aircraft_id}/reservations/new",
        {"start_dt": _fmt(downtime_res_start), "end_dt": _fmt(downtime_res_end)},
    )
    with app.app_context():
        from models import Reservation  # pyright: ignore[reportMissingImports]

        res_downtime = (
            Reservation.query.filter_by(
                aircraft_id=aircraft_id, start_dt=downtime_res_start
            )
            .order_by(Reservation.id.desc())
            .first()
        )
        res_downtime_id = res_downtime.id

    downtime_confirm_resp = submit(
        owner,
        f"/aircraft/{aircraft_id}/reservations/{res_downtime_id}/confirm",
        {},
        expect_error=True,
    )
    assert b"Cannot confirm: overlaps" in downtime_confirm_resp.data

    # Owner opens a grounding snag: a *new* (non-conflicting) reservation
    # request now succeeds but with a warning — checked at creation time,
    # default policy ("warn").
    submit(
        owner,
        f"/aircraft/{aircraft_id}/snags/new",
        {"title": "Flat tyre", "is_grounding": "1"},
    )
    with app.app_context():
        from models import Snag  # pyright: ignore[reportMissingImports]

        snag = Snag.query.filter_by(aircraft_id=aircraft_id, title="Flat tyre").first()
        snag_id = snag.id

    grounded_slot_start = datetime(2024, 7, 3, 9, 0, tzinfo=timezone.utc)
    grounded_slot_end = grounded_slot_start + timedelta(hours=1)
    warn_resp = submit(
        user_b,
        f"/aircraft/{aircraft_id}/reservations/new",
        {"start_dt": _fmt(grounded_slot_start), "end_dt": _fmt(grounded_slot_end)},
    )
    assert b"check its status before flying" in warn_resp.data

    # Flip the tenant policy to "block" (no route exists for this field —
    # sanctioned direct write, see module docstring) -> the same kind of
    # request is now rejected outright, at creation time.
    with app.app_context():
        from models import TenantProfile, db  # pyright: ignore[reportMissingImports]

        profile = TenantProfile.query.filter_by(tenant_id=owner_env.tenant_id).first()
        profile.grounded_reservation_policy = "block"
        db.session.commit()

    blocked_slot_start = datetime(2024, 7, 4, 9, 0, tzinfo=timezone.utc)
    blocked_slot_end = blocked_slot_start + timedelta(hours=1)
    block_resp = submit(
        user_b,
        f"/aircraft/{aircraft_id}/reservations/new",
        {"start_dt": _fmt(blocked_slot_start), "end_dt": _fmt(blocked_slot_end)},
        expect_error=True,
    )
    assert b"requires bookings to be blocked while grounded" in block_resp.data

    # Resolve the snag -> the same slot now succeeds (no warning, no error).
    submit(
        owner,
        f"/aircraft/{aircraft_id}/snags/{snag_id}/resolve",
        {"resolution_note": "Tyre replaced"},
    )
    resolved_resp = submit(
        user_b,
        f"/aircraft/{aircraft_id}/reservations/new",
        {"start_dt": _fmt(blocked_slot_start), "end_dt": _fmt(blocked_slot_end)},
    )
    assert b"check its status before flying" not in resolved_resp.data

    # Cancel user A's original (confirmed) reservation -> user B's earlier
    # overlapping request for that same slot can now be confirmed.
    submit(user_a, f"/aircraft/{aircraft_id}/reservations/{res_a_id}/cancel", {})
    submit(
        owner,
        f"/aircraft/{aircraft_id}/reservations/{res_b_overlap_id}/confirm",
        {},
    )

    with app.app_context():
        from models import Reservation, ReservationStatus  # pyright: ignore[reportMissingImports]

        assert (
            Reservation.query.filter_by(id=res_a_id).first().status
            == ReservationStatus.CANCELLED
        )
        confirmed_b = Reservation.query.filter_by(id=res_b_overlap_id).first()
        assert confirmed_b.status == ReservationStatus.CONFIRMED
        assert confirmed_b.pilot_user_id != res_a_pilot_id
