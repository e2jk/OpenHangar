"""J12 — Role x write-route matrix (docs/functional_test_plan.md).

Intent: the role table implied by AGENTS.md ("Roles are defined in
app/models.py: ADMIN, OWNER, PILOT, MAINTENANCE, VIEWER, STUDENT,
INSTRUCTOR") is enforced on every state-changing route, not just the
"representative routes" spot-checked by test_multi_user.py /
test_authorization.py.

Scope note (documented deviation): the plan's own text names six roles
including "RENTER", but app/models.py's Role enum has no RENTER — renting
is just Role.PILOT plus a RenterAuthorization row — and does have
STUDENT/INSTRUCTOR, which carry real decorator-level distinctions
(require_pilot_access passes STUDENT/INSTRUCTOR; aircraft.py's narrower
_PILOT_ROLES tuple does not). This test uses the seven roles that
actually exist.

One tenant, one aircraft, seven logged-in clients (one per role, all
granted access to the shared aircraft so the per-aircraft-grant layer
that `_get_aircraft_or_404` also enforces isn't conflated with the role
layer this test targets). For every POST-capable rule in `app.url_map`,
classify it as either present in ENDPOINT_TABLE (an expected allow-set
of roles) or in _EXCLUDED_ENDPOINTS (with a one-line reason) -- an
unclassified route fails the test loudly, so a newly added write route
can't silently skip this sweep (same forcing function as J11).

Requests use a non-existent id (_FAKE_ID) for every int URL argument
except on the four reservation routes whose access control is an inline
ownership check rather than a role decorator (edit/cancel/checkout/
checkin) -- those need a *real* aircraft + reservation to ever reach the
ownership check at all (a fake id 404s at the aircraft/reservation
lookup, before the ownership branch runs). Every other route's role gate
is a decorator that runs before any DB lookup, so a fake id safely
distinguishes "blocked by role" (403) from "role passed, then 404'd on a
made-up id" (not 403) without needing a full object graph -- building
real components/expenses/snags/documents/etc. for every route would
duplicate J1-J6 and the unit suite's coverage of *those* routes' business
logic, which isn't this test's concern.

Consequently this test asserts "denied roles get exactly 403" / "allowed
roles get anything other than 403" rather than the plan's literal
"2xx/3xx vs 403" wording -- for a route reached with a fake id, an
allowed role legitimately sees a 404, not a 2xx/3xx, and that 404 is
still the correct proof that the *role* layer let the request through.
Actual 2xx success for legitimate input is already exhaustively covered
by J1-J6 and the ~3000-test unit suite (per the plan's own "Existing"
notes for this journey).
"""

from datetime import datetime, timedelta, timezone

import pytest  # pyright: ignore[reportMissingImports]

from tests.functional.conftest import second_user, submit

_FAKE_ID = 999999

ALL_ROLES = (
    "admin",
    "owner",
    "pilot",
    "maintenance",
    "viewer",
    "student",
    "instructor",
)

# Role-sets named after the decorator/tuple that produces them in the app
# (see app/utils.py and each blueprint's local role tuples).
_OWNER_ROLES = {"admin", "owner"}
_PILOT_ROLES_STRICT = {"admin", "owner", "pilot"}  # aircraft.py's _PILOT_ROLES
_CREW_ROLES = {"admin", "owner", "pilot", "maintenance"}  # airworthiness/snags
_MAINT_ROLES = {"admin", "owner", "maintenance"}  # maintenance.py/_DOWNTIME_ROLES
_BOOKING_ROLES = {"admin", "owner", "pilot"}  # reservations._BOOKING_ROLES
_PILOT_ACCESS_ROLES = {
    "admin",
    "owner",
    "pilot",
    "student",
    "instructor",
}  # require_pilot_access
_ANY_ROLE = set(ALL_ROLES)  # login_required only, no role gate

# The four reservation routes below gate on an inline ownership check
# (ADMIN/OWNER bypass, else `pilot_user_id == caller`), not a role tuple.
# This fixture's reservation is owned by the "pilot" client, so the
# *observed* allow-set coincides with _BOOKING_ROLES -- documented here
# rather than modelled as a distinct mechanism, since the resulting table
# entry is identical.

ENDPOINT_TABLE: dict[str, set[str]] = {
    # aircraft_bp
    "aircraft.new_aircraft": _OWNER_ROLES,
    "aircraft.edit_aircraft": _OWNER_ROLES,
    "aircraft.archive_aircraft": _OWNER_ROLES,
    "aircraft.unarchive_aircraft": _OWNER_ROLES,
    "aircraft.delete_aircraft": _OWNER_ROLES,
    "aircraft.quick_add_components": _OWNER_ROLES,
    "aircraft.new_component": _OWNER_ROLES,
    "aircraft.edit_component": _OWNER_ROLES,
    "aircraft.delete_component": _OWNER_ROLES,
    "aircraft.wb_config": _OWNER_ROLES,
    "aircraft.wb_entry": _PILOT_ROLES_STRICT,
    "aircraft.wb_entry_delete": _PILOT_ROLES_STRICT,
    "aircraft.gps_import_upload": _PILOT_ROLES_STRICT,
    "aircraft.gps_import_confirm_one": _PILOT_ROLES_STRICT,
    "aircraft.gps_import_rollback": _OWNER_ROLES,
    "aircraft.upload_photo": _OWNER_ROLES,
    "aircraft.delete_photo": _OWNER_ROLES,
    "aircraft.reorder_photos": _OWNER_ROLES,
    # airworthiness_bp
    "airworthiness.add_node": _OWNER_ROLES,
    "airworthiness.delete_node": _OWNER_ROLES,
    "airworthiness.add_document": _OWNER_ROLES,
    "airworthiness.delete_document": _OWNER_ROLES,
    "airworthiness.update_status": _CREW_ROLES,
    "airworthiness.add_stc": _OWNER_ROLES,
    "airworthiness.delete_stc": _OWNER_ROLES,
    # maintenance_bp
    "maintenance.new_trigger": _MAINT_ROLES,
    "maintenance.edit_trigger": _MAINT_ROLES,
    "maintenance.delete_trigger": _MAINT_ROLES,
    "maintenance.service_trigger": _MAINT_ROLES,
    # snags_bp
    "snags.new_snag": _CREW_ROLES,
    "snags.edit_snag": _CREW_ROLES,
    "snags.resolve_snag": _CREW_ROLES,
    "snags.delete_snag": _CREW_ROLES,
    # expenses_bp
    "expenses.add_expense": _OWNER_ROLES,
    "expenses.edit_expense": _OWNER_ROLES,
    "expenses.delete_expense": _OWNER_ROLES,
    # documents_bp
    "documents.upload_document": _OWNER_ROLES,
    "documents.edit_document": _OWNER_ROLES,
    "documents.delete_document": _OWNER_ROLES,
    "documents.upload_insurance_cert": _OWNER_ROLES,
    "documents.upload_pilot_document": _ANY_ROLE,
    "documents.delete_pilot_document": _ANY_ROLE,
    "documents.scan_documents": _OWNER_ROLES,
    "documents.rename_reconcile_folder": _OWNER_ROLES,
    "documents.import_reconcile": _OWNER_ROLES,
    "documents.ignore_reconcile": _OWNER_ROLES,
    # flights_bp
    "flights.log_flight": _PILOT_ACCESS_ROLES,
    "flights.edit_flight": _PILOT_ACCESS_ROLES,
    "flights.parse_gps_api": _PILOT_ACCESS_ROLES,
    "flights.delete_flight": _PILOT_ACCESS_ROLES,
    "flights.airframe_import_upload": _OWNER_ROLES,
    "flights.airframe_import_execute": _OWNER_ROLES,
    "flights.airframe_import_rollback": _OWNER_ROLES,
    # offline_bp
    "offline.sync_flight": _PILOT_ACCESS_ROLES,
    "offline.sync_pilot_entry": _PILOT_ACCESS_ROLES,
    # pilots_bp -- self-scoped pilot logbook/minimums, all require_pilot_access
    "pilots.minimums_create": _PILOT_ACCESS_ROLES,
    "pilots.minimums_revise": _PILOT_ACCESS_ROLES,
    "pilots.minimums_section_add": _PILOT_ACCESS_ROLES,
    "pilots.minimums_section_edit": _PILOT_ACCESS_ROLES,
    "pilots.minimums_section_delete": _PILOT_ACCESS_ROLES,
    "pilots.minimums_section_move_up": _PILOT_ACCESS_ROLES,
    "pilots.minimums_section_move_down": _PILOT_ACCESS_ROLES,
    "pilots.minimums_item_add": _PILOT_ACCESS_ROLES,
    "pilots.minimums_item_edit": _PILOT_ACCESS_ROLES,
    "pilots.minimums_item_delete": _PILOT_ACCESS_ROLES,
    "pilots.minimums_item_move_up": _PILOT_ACCESS_ROLES,
    "pilots.minimums_item_move_down": _PILOT_ACCESS_ROLES,
    "pilots.minimums_publish": _PILOT_ACCESS_ROLES,
    "pilots.minimums_delete_draft": _PILOT_ACCESS_ROLES,
    "pilots.new_entry": _PILOT_ACCESS_ROLES,
    "pilots.edit_entry": _PILOT_ACCESS_ROLES,
    "pilots.delete_entry": _PILOT_ACCESS_ROLES,
    "pilots.import_upload": _PILOT_ACCESS_ROLES,
    "pilots.import_execute": _PILOT_ACCESS_ROLES,
    "pilots.import_rollback": _PILOT_ACCESS_ROLES,
    "pilots.pilot_gps_import_upload": _PILOT_ACCESS_ROLES,
    "pilots.pilot_gps_import_confirm_one": _PILOT_ACCESS_ROLES,
    "pilots.profile": _PILOT_ACCESS_ROLES,
    # pwa_bp
    "pwa.share_target": _ANY_ROLE,
    "pwa.share_confirm": _ANY_ROLE,
    # reservations_bp
    "reservations.downtime_new": _MAINT_ROLES,
    "reservations.downtime_edit": _MAINT_ROLES,
    "reservations.downtime_delete": _MAINT_ROLES,
    "reservations.new_reservation": _BOOKING_ROLES,
    "reservations.edit_reservation": _BOOKING_ROLES,  # ownership, see module docstring
    "reservations.cancel_reservation": _BOOKING_ROLES,  # ownership
    "reservations.confirm_reservation": _OWNER_ROLES,
    "reservations.decline_reservation": _OWNER_ROLES,
    "reservations.booking_settings": _OWNER_ROLES,
    "reservations.checkout": _BOOKING_ROLES,  # ownership
    "reservations.checkin": _BOOKING_ROLES,  # ownership
    "reservations.rental_charge": _OWNER_ROLES,
    # share_bp
    "share.create_token": _OWNER_ROLES,
    "share.revoke_token": _OWNER_ROLES,
    # users_bp
    "users.invite": _OWNER_ROLES,
    "users.change_role": _OWNER_ROLES,
    "users.revoke_access": _OWNER_ROLES,
    "users.revoke_invite": _OWNER_ROLES,
    "users.update_aircraft_access": _OWNER_ROLES,
    "users.toggle_all_planes": _OWNER_ROLES,
    "users.update_user_flags": _OWNER_ROLES,
    "users.edit_permissions": _OWNER_ROLES,
    # config_bp -- before_request forces ADMIN/OWNER tenant-wide except
    # notification_preferences, which is explicitly exempted in the code.
    "config.update_tenant_slug": _OWNER_ROLES,
    "config.update_map_tiles": _OWNER_ROLES,
    "config.update_profile": _OWNER_ROLES,
    "config.notification_preferences": _ANY_ROLE,
    "config.renter_add": _OWNER_ROLES,
    "config.renter_edit": _OWNER_ROLES,
    "config.renter_revoke": _OWNER_ROLES,
    "config.renter_record_payment": _OWNER_ROLES,
    # auth_bp -- self-service, any role, by design
    "auth.profile": _ANY_ROLE,
}

# Routes with a POST method that this sweep deliberately does not drive.
_EXCLUDED_ENDPOINTS = {
    # Real external side effects if actually invoked by an allowed
    # (ADMIN/OWNER) role -- pg_dump subprocess, a real SMTP send, a real
    # network fetch. Their role gate is the *same* config_bp.before_request
    # ADMIN/OWNER check already exercised by other config.* rows in
    # ENDPOINT_TABLE, so excluding them loses no gating coverage.
    "config.run_backup_now": "runs a real pg_dump subprocess backup",
    "config.test_email": "sends a real email via send_email()",
    "config.check_version": "makes a real outbound network fetch",
    # Instance-admin routes: a different, non-tenant-role access model
    # (require_instance_admin), explicitly out of scope for this
    # per-tenant-role matrix.
    "config.trigger_upgrade": "require_instance_admin, not a tenant role",
    "config.tenant_create": "require_instance_admin, not a tenant role",
    "config.tenant_toggle_active": "require_instance_admin, not a tenant role",
    "config.tenant_reset_owner_password": "require_instance_admin, not a tenant role",
    "config.backfill_aircraft_type_icao": "require_instance_admin, not a tenant role",
    "config.backfill_pilot_log_to_flight_entries": (
        "require_instance_admin, not a tenant role"
    ),
    # Pre-auth / token-based flows: no tenant role exists yet at request time.
    "auth.login": "pre-auth, no tenant role exists yet",
    "auth.setup": "pre-auth first-run tenant creation",
    "auth.reset_password": "token-based pre-auth password reset",
    "users.accept_invite": "token-based pre-auth invite acceptance",
    "demo.enter": "pre-auth demo sandbox entry point",
    # A second, non-role guard (`if OPENHANGAR_ENV != "production": abort(403)`,
    # airworthiness/routes.py) stops the real EASA sync from firing outside
    # prod. tests/e2e/conftest.py sets OPENHANGAR_ENV="development" at import
    # time, which leaks into the rest of the process once pytest collects it
    # (even without running an e2e test) -- in that state this route 403s for
    # every role, unrelated to the role gate this sweep tests. Its role
    # decorator (_OWNER_ROLES) is already covered by many other rows above.
    "airworthiness.trigger_sync": "env-gated to production only, see comment",
}


@pytest.fixture
def role_world(owner_env, app, client_factory):
    admin = owner_env.client
    aircraft_id = owner_env.aircraft_id

    submit(
        admin,
        "/config/profile",
        {
            "operating_model": "sole_operator",
            "planned_aircraft_count": "1",
            "allows_rental": "on",
        },
    )
    submit(
        admin,
        f"/aircraft/{aircraft_id}/reservations/settings",
        {"hourly_rate": "100.00", "rate_type": "wet", "rate_basis": "engine_time"},
    )

    clients = {"admin": admin}
    non_owner_roles = (
        "owner",
        "pilot",
        "maintenance",
        "viewer",
        "student",
        "instructor",
    )
    for role in non_owner_roles:
        clients[role] = second_user(
            app,
            client_factory,
            admin,
            role,
            f"role-{role}@example.com",
            f"Role {role.title()}",
            aircraft_ids=str(aircraft_id),
        )

    start_dt = datetime(2030, 6, 20, 9, 0, tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(hours=2)
    submit(
        clients["pilot"],
        f"/aircraft/{aircraft_id}/reservations/new",
        {
            "start_dt": start_dt.strftime("%Y-%m-%dT%H:%M"),
            "end_dt": end_dt.strftime("%Y-%m-%dT%H:%M"),
        },
    )

    with app.app_context():
        from models import (  # pyright: ignore[reportMissingImports]
            Reservation,
            User,
        )

        pilot_id = User.query.filter_by(email="role-pilot@example.com").first().id
        reservation = Reservation.query.filter_by(
            aircraft_id=aircraft_id, pilot_user_id=pilot_id
        ).first()
        assert reservation is not None
        res_id = reservation.id

    return clients, aircraft_id, res_id


_OWNERSHIP_ENDPOINTS = {
    "reservations.edit_reservation",
    "reservations.cancel_reservation",
    "reservations.checkout",
    "reservations.checkin",
}


def test_role_write_matrix_over_full_url_map(app, role_world):
    clients, real_aircraft_id, real_res_id = role_world

    unmapped: list[str] = []
    checked = 0

    with app.app_context():
        adapter = app.url_map.bind("localhost")
        seen_endpoints: set[str] = set()
        for rule in app.url_map.iter_rules():
            if "POST" not in rule.methods:
                continue
            endpoint = rule.endpoint
            if endpoint in seen_endpoints:
                continue
            seen_endpoints.add(endpoint)

            if endpoint in _EXCLUDED_ENDPOINTS:
                continue
            if endpoint not in ENDPOINT_TABLE:
                unmapped.append(endpoint)
                continue

            allowed_roles = ENDPOINT_TABLE[endpoint]
            params = {}
            for arg in rule.arguments:
                if endpoint in _OWNERSHIP_ENDPOINTS and arg == "aircraft_id":
                    params[arg] = real_aircraft_id
                elif endpoint in _OWNERSHIP_ENDPOINTS and arg == "res_id":
                    params[arg] = real_res_id
                else:
                    params[arg] = _FAKE_ID
            method = "GET" if "GET" in rule.methods else "POST"
            path = adapter.build(endpoint, params, method=method)

            for role in ALL_ROLES:
                client = clients[role]
                if method == "GET":
                    resp = client.get(path, follow_redirects=False)
                else:
                    resp = client.post(path, data={}, follow_redirects=False)
                checked += 1
                if role in allowed_roles:
                    assert resp.status_code != 403, (
                        f"{endpoint} {path}: role={role} expected to pass the "
                        f"role gate, got 403"
                    )
                else:
                    assert resp.status_code == 403, (
                        f"{endpoint} {path}: role={role} expected to be denied "
                        f"(403), got {resp.status_code}"
                    )

    assert not unmapped, (
        "New POST route(s) not classified in ENDPOINT_TABLE or "
        f"_EXCLUDED_ENDPOINTS -- classify them: {unmapped}"
    )
    assert checked > 400, f"sweep only checked {checked} role x route cells"
