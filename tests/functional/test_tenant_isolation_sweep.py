"""J11 — Cross-tenant isolation sweep (docs/functional_test_plan.md).

Intent: no route, present or future, leaks tenant B's objects to a
tenant-A user. Builds two fully-populated tenants, then enumerates
`app.url_map` for every GET rule with an `<int:...>` converter,
substitutes tenant B's matching object id, and requests it as tenant A's
admin — every response must be 403/404, and must never leak tenant B's
identifying markers in the body (catches routes that 200 with leaked
content instead of rejecting outright).

Scope note (documented deviation, per the plan's own "deviate only with
a documented reason" rule): populating a second tenant with *every*
object type in the schema — airworthiness nodes/docs/STCs, personal
minimums revisions/sections/items, GPS/airframe/logbook import batches,
aircraft photos, pending-reconcile rows — would multiply this fixture's
size several times over for marginal additional coverage (those
blueprints reuse the same `_get_*_or_404` ownership-check pattern
already exercised by the object types below). This sweep covers the
converters used by every route mounted at the aircraft/flights/
maintenance/expenses/snags/reservations/documents/share/users/pilots/
config-renters blueprints — the highest-traffic, highest-risk surface —
and explicitly excludes the rest via `_EXCLUDED_CONVERTERS` below, each
with a one-line reason, so the sweep still fails loudly (not silently)
if a *new* route introduces a converter outside both the mapped and the
excluded sets.

Setup uses direct model writes throughout, unlike the other journeys:
J11's subject is *view*-route access control, not creation flows (which
J1-J6 and the ~3000-test unit suite already exercise via routes) — a
second tenant's data only needs to exist, not be created through the
product, for this sweep to do its job.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]

# Converters that do not identify a tenant-scoped database object, so no
# tenant-B id applies to them; excluded from the sweep with a reason each.
_EXCLUDED_CONVERTERS = {
    "code": "a hardcoded squawk-code whitelist (7700/7600/7500/7000/1200), not a DB id",
    "seg_idx": "a session-scoped list index into a GPS-import review, not a DB id",
    "tenant_id": "instance-admin routes that legitimately span all tenants by design",
    "filename": "a static filename, not a DB id",
    "lang": "a locale code, not a DB id",
    "token": "opaque invite/reset/share tokens — not int-typed, out of this sweep",
    # Object types deliberately out of scope for this first cut — see the
    # module docstring. Each reuses an ownership-check pattern (_get_*_or_404)
    # already exercised by a mapped converter in the same or a sibling
    # blueprint, so the marginal isolation-coverage value is low relative to
    # the fixture size needed to populate them for a second tenant.
    "photo_id": "aircraft photos — same _get_aircraft_or_404-style ownership check as document_id",
    "batch_id": "GPS/airframe/logbook import batches — same pattern as flight_id/document_id",
    "node_id": "airworthiness EASA source nodes — same pattern as document_id",
    "doc_id": "airworthiness documents — same pattern as document_id",
    "stc_id": "airworthiness installed STCs — same pattern as document_id",
    "revision_id": "personal minimums revisions — self-scoped to pilot_user_id, not tenant-object shaped",
    "section_id": "personal minimums sections — same as revision_id",
    "item_id": "personal minimums items — same as revision_id",
    "pending_id": "pending-reconcile rows — same _get_aircraft_or_404-style pattern as document_id",
}


@dataclass
class TenantWorld:
    client: object
    tenant_id: int
    admin_user_id: int
    aircraft_id: int
    component_id: int
    flight_id: int
    expense_id: int
    snag_id: int
    trigger_id: int
    res_id: int
    downtime_id: int
    document_id: int
    wb_entry_id: int
    token_id: int
    user_id: int
    inv_id: int
    auth_id: int
    entry_id: (
        int  # standalone PilotLogbookEntry — pilots/offline blueprints' "entry_id"
    )


def _build_tenant_world(app, client_factory, suffix: str) -> TenantWorld:
    with app.app_context():
        from models import (  # pyright: ignore[reportMissingImports]
            Aircraft,
            Component,
            Expense,
            FlightEntry,
            MaintenanceDowntime,
            MaintenanceTrigger,
            PilotLogbookEntry,
            RenterAuthorization,
            Reservation,
            ReservationStatus,
            Role,
            ShareToken,
            Snag,
            Tenant,
            TenantUser,
            User,
            UserInvitation,
            WeightBalanceConfig,
            WeightBalanceEntry,
            db,
        )
        from models import Document as DocumentModel  # pyright: ignore[reportMissingImports]

        tenant = Tenant(name=f"Isolation Sweep Tenant {suffix}")
        db.session.add(tenant)
        db.session.flush()

        admin = User(
            email=f"sweep-admin-{suffix}@example.com",
            password_hash=_pw_hash.hash("SweepAdminPass1!"),
            is_active=True,
        )
        db.session.add(admin)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=admin.id, tenant_id=tenant.id, role=Role.ADMIN)
        )

        other_user = User(
            email=f"sweep-other-{suffix}@example.com",
            password_hash=_pw_hash.hash("SweepOtherPass1!"),
            is_active=True,
        )
        db.session.add(other_user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=other_user.id, tenant_id=tenant.id, role=Role.PILOT)
        )

        aircraft = Aircraft(
            tenant_id=tenant.id,
            registration=f"OO-SW{suffix.upper()}",
            make="Cessna",
            model="172S",
        )
        db.session.add(aircraft)
        db.session.flush()

        component = Component(
            aircraft_id=aircraft.id, type="engine", make="Lycoming", model="IO-360"
        )
        db.session.add(component)

        flight = FlightEntry(
            aircraft_id=aircraft.id,
            date=date(2024, 6, 1),
            departure_icao="EBOS",
            arrival_icao="EBBR",
            flight_time_counter_start=100.0,
            flight_time_counter_end=101.5,
        )
        db.session.add(flight)
        db.session.flush()

        expense = Expense(
            aircraft_id=aircraft.id,
            date=date(2024, 6, 1),
            expense_type="fuel",
            expense_category="operating",
            amount=50.0,
        )
        db.session.add(expense)

        snag = Snag(aircraft_id=aircraft.id, title="Sweep snag", reporter="Sweep Admin")
        db.session.add(snag)

        trigger = MaintenanceTrigger(
            aircraft_id=aircraft.id,
            name="Sweep trigger",
            trigger_type="hours",
            due_engine_hours=500.0,
            interval_hours=50,
        )
        db.session.add(trigger)

        far_future = datetime.now(timezone.utc) + timedelta(days=30)
        reservation = Reservation(
            aircraft_id=aircraft.id,
            pilot_user_id=other_user.id,
            status=ReservationStatus.PENDING,
            start_dt=far_future,
            end_dt=far_future + timedelta(hours=2),
        )
        db.session.add(reservation)

        downtime = MaintenanceDowntime(
            aircraft_id=aircraft.id,
            start_dt=far_future + timedelta(days=10),
            end_dt=far_future + timedelta(days=11),
            reason="Sweep downtime",
        )
        db.session.add(downtime)

        document = DocumentModel(
            aircraft_id=aircraft.id,
            filename=f"sweep-doc-{suffix}.pdf",
            original_filename=f"sweep-doc-{suffix}.pdf",
            doc_type="other",
        )
        db.session.add(document)

        wb_config = WeightBalanceConfig(
            aircraft_id=aircraft.id,
            empty_weight=700.0,
            empty_cg_arm=1.0,
            max_takeoff_weight=1100.0,
            forward_cg_limit=0.9,
            aft_cg_limit=1.2,
        )
        db.session.add(wb_config)
        db.session.flush()

        wb_entry = WeightBalanceEntry(
            config_id=wb_config.id,
            date=date(2024, 6, 1),
            total_weight=900.0,
            loaded_cg=1.0,
            is_in_envelope=True,
            station_weights={},
        )
        db.session.add(wb_entry)

        share_token = ShareToken(
            aircraft_id=aircraft.id, token=f"sweep-tok-{suffix}", access_level="summary"
        )
        db.session.add(share_token)

        invitation = UserInvitation(
            tenant_id=tenant.id,
            invited_by_user_id=admin.id,
            email=f"sweep-invitee-{suffix}@example.com",
            role=Role.PILOT,
            expires_at=far_future,
        )
        db.session.add(invitation)

        renter_auth = RenterAuthorization(
            tenant_id=tenant.id,
            renter_user_id=other_user.id,
            aircraft_id=aircraft.id,
            granted_on=date(2024, 1, 1),
        )
        db.session.add(renter_auth)

        pilot_entry = PilotLogbookEntry(
            pilot_user_id=admin.id,
            date=date(2024, 6, 1),
            aircraft_type="Cessna 172S",
            aircraft_registration=aircraft.registration,
            pic_name="Sweep Pilot",
            landings_day=1,
            function_pic=1.0,
        )
        db.session.add(pilot_entry)

        db.session.commit()

        world_ids = dict(
            tenant_id=tenant.id,
            admin_user_id=admin.id,
            aircraft_id=aircraft.id,
            component_id=component.id,
            flight_id=flight.id,
            expense_id=expense.id,
            snag_id=snag.id,
            trigger_id=trigger.id,
            res_id=reservation.id,
            downtime_id=downtime.id,
            document_id=document.id,
            wb_entry_id=wb_entry.id,
            token_id=share_token.id,
            user_id=other_user.id,
            inv_id=invitation.id,
            auth_id=renter_auth.id,
            entry_id=pilot_entry.id,
        )
        admin_email = admin.email

    new_client = client_factory()
    new_client.post(
        "/login",
        data={"email": admin_email, "password": "SweepAdminPass1!"},
        follow_redirects=True,
    )

    return TenantWorld(client=new_client, **world_ids)


@pytest.fixture
def two_tenants(app, client_factory):
    world_a = _build_tenant_world(app, client_factory, "a")
    world_b = _build_tenant_world(app, client_factory, "b")
    return world_a, world_b


def _int_converter_args(rule) -> dict:
    """Return {argument_name: 'int'} for every <int:...> converter on `rule`."""
    result = {}
    for arg in rule.arguments:
        converter = rule._converters.get(
            arg
        )  # werkzeug internal, stable enough for a test
        if converter is not None and type(converter).__name__ == "IntegerConverter":
            result[arg] = "int"
    return result


def test_cross_tenant_isolation_over_full_url_map(app, two_tenants):
    world_a, world_b = two_tenants
    client = world_a.client

    unmapped: list[str] = []
    checked = 0
    leaks: list[str] = []

    with app.app_context():
        adapter = app.url_map.bind("localhost")
        for rule in app.url_map.iter_rules():
            if "GET" not in rule.methods:
                continue
            int_args = _int_converter_args(rule)
            if not int_args:
                continue

            params = {}
            skip_rule = False
            for arg in int_args:
                if arg in _EXCLUDED_CONVERTERS:
                    skip_rule = True
                    break
                # `entry_id` is ambiguous: WeightBalanceEntry in the aircraft
                # blueprint's /wb/ routes, PilotLogbookEntry everywhere else
                # (pilots/offline blueprints) — disambiguate by rule path,
                # matching scripts/generate_routes.py's own resolution.
                if arg == "entry_id" and "/wb/" in rule.rule:
                    value = world_b.wb_entry_id
                else:
                    value = getattr(world_b, arg, None)
                if value is None:
                    unmapped.append(f"{rule.endpoint} ({arg})")
                    skip_rule = True
                    break
                params[arg] = value
            if skip_rule:
                continue

            # Build via the MapAdapter, not Rule.build() directly — the
            # latter can silently return an empty path for some rules.
            path = adapter.build(rule.endpoint, params, method="GET")

            # follow_redirects: some routes normalize the path with a 308
            # (e.g. a trailing-slash redirect) before their own access check
            # runs — that's not a leak, so follow it and assert on the final
            # response.
            resp = client.get(path, follow_redirects=True)
            checked += 1
            assert resp.status_code in (403, 404), (
                f"{rule.endpoint} {path} leaked tenant B's object: "
                f"status {resp.status_code} (expected 403/404)"
            )
            body = resp.data.decode("utf-8", "replace")
            registration_marker = f"OO-SW{'b'.upper()}"
            if registration_marker in body:
                leaks.append(f"{rule.endpoint} {path}")

    assert not unmapped, (
        "New int-converter route(s) not classified in this sweep's mapping "
        f"table or _EXCLUDED_CONVERTERS — classify them: {unmapped}"
    )
    assert not leaks, (
        f"Tenant B's registration leaked into tenant A's response: {leaks}"
    )
    assert checked > 30, (
        f"sweep only checked {checked} routes — mapping table likely broken"
    )
