"""J18 — Instance-admin provisioning (docs/functional_test_plan.md).

Intent: a super-admin provisions a second tenant -> its admin logs in,
completes setup -> J11's isolation assertions hold between the two
tenants created *this* way (provisioning path, not fixture path).

Documented deviation (the plan's own "deviate only with a documented
reason" rule): "its admin logs in, completes setup" doesn't literally
mean the /setup wizard -- that route is gated globally by
`User.query.count() == 0` (app/auth/routes.py's _no_users(), confirmed
in J13), not per-tenant, so it's unreachable for a second tenant's admin
once any user (the super-admin) already exists. config.tenant_create
also never creates a password/login directly -- it only creates the
Tenant, a TenantProfile, and a 7-day UserInvitation(role=OWNER). The
real, code-accurate sequence is: super-admin POSTs
/config/tenants/create -> the new admin accepts that invitation via the
same /config/users/invite/<token> flow every other invited user goes
through (app/users/routes.py) -> logs in for the first time -> sets
their operating model via /config/profile, the closest real analog to
"finishing setup" for a tenant whose profile row already exists.

Smaller scope than J11's exhaustive url_map sweep, deliberately: J11
already proves the general tenant-scoping mechanism holds across ~60
routes for two fixture-built tenants; that mechanism doesn't change
based on how a tenant came to exist, so re-running the full sweep here
would test the same code path twice for no added value. What's
genuinely new is confirming a tenant built through the *provisioning*
route specifically gets the same isolation as any other tenant -- so
this journey builds tenant B's data through real routes (as its own new
admin, exercising their freshly-granted OWNER access) and checks
cross-tenant 404s on the handful of object types it creates, rather
than re-deriving J11's full converter-classification machinery for a
partially-populated second tenant.

Existing: test_instance_admin.py covers tenant_create's DB effects and
require_instance_admin gating in isolation (session-stuffed login, no
invite-accept, no cross-tenant check); test_tenant_isolation_sweep.py
(J11) covers the general isolation mechanism exhaustively for two
fixture-built tenants. Neither chains provisioning -> invite-accept ->
real login -> isolation into one flow.
"""

import re

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import User, db  # pyright: ignore[reportMissingImports]

from tests.functional.conftest import submit


def test_provisioned_tenant_isolated_from_first(owner_env, app, client_factory):
    tenant_a = owner_env.client
    aircraft_a = owner_env.aircraft_id

    # Sanctioned direct write: is_instance_admin has no UI grant path at
    # all (confirmed in this session's J14 research) -- a direct write is
    # the only way to create one.
    with app.app_context():
        super_admin = User(
            email="super-admin@example.com",
            password_hash=_pw_hash.hash("SuperAdminPass1!"),
            is_instance_admin=True,
            is_active=True,
        )
        db.session.add(super_admin)
        db.session.commit()

    super_admin_client = client_factory()
    submit(
        super_admin_client,
        "/login",
        {"email": "super-admin@example.com", "password": "SuperAdminPass1!"},
    )

    # Provision a second tenant. No password is created here -- only an
    # invitation.
    create_resp = submit(
        super_admin_client,
        "/config/tenants/create",
        {
            "name": "Provisioned Hangar",
            "admin_email": "provisioned-admin@example.com",
            "operating_model": "sole_operator",
        },
    )
    token_match = re.search(rb"/config/users/invite/([A-Za-z0-9_-]+)", create_resp.data)
    assert token_match, "invite link not found in tenant_create's flash"
    invite_token = token_match.group(1).decode()

    # The new admin accepts the invitation -- this is the step that
    # actually creates their User row and password.
    tenant_b = client_factory()
    accept_url = f"/config/users/invite/{invite_token}"
    submit(
        tenant_b,
        accept_url,
        {
            "email": "provisioned-admin@example.com",
            "password": "ProvisionedPass1!",
            "password2": "ProvisionedPass1!",
        },
    )
    submit(
        tenant_b,
        "/login",
        {"email": "provisioned-admin@example.com", "password": "ProvisionedPass1!"},
    )

    # Closest real analog to "completes setup" for a tenant whose profile
    # already exists (see module docstring).
    submit(
        tenant_b,
        "/config/profile",
        {"operating_model": "sole_operator", "planned_aircraft_count": "1"},
    )

    # The new admin has real OWNER access -- build their tenant's data
    # through the product, the same as owner_env does for tenant A.
    resp = tenant_b.post(
        "/aircraft/new",
        data={"registration": "OO-PROV", "make": "Cessna", "model": "172S"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    match = re.search(r"/aircraft/(\d+)", resp.headers["Location"])
    assert match
    aircraft_b = int(match.group(1))
    submit(
        tenant_b,
        f"/aircraft/{aircraft_b}/expenses/add",
        {
            "date": "2024-06-01",
            "expense_type": "fuel",
            "expense_category": "operating",
            "amount": "50.00",
        },
    )

    # Isolation holds in both directions for the tenant built via the
    # provisioning route, exactly as it does for any other tenant.
    assert tenant_a.get(f"/aircraft/{aircraft_b}").status_code == 404
    assert tenant_b.get(f"/aircraft/{aircraft_a}").status_code == 404

    aircraft_b_page = tenant_a.get(f"/aircraft/{aircraft_b}", follow_redirects=True)
    assert b"OO-PROV" not in aircraft_b_page.data

    aircraft_a_page = tenant_b.get(f"/aircraft/{aircraft_a}", follow_redirects=True)
    assert b"OO-TST" not in aircraft_a_page.data
