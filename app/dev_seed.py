"""
Development seed data.
Creates a default admin user + tenant and a sample fleet so the app is
usable immediately after a fresh dev install without going through the
setup wizard.

Fleet content lives in _seed_helpers.py and is shared with demo_seed.
Credentials printed to container logs on first run.
Never loaded in production.
"""

import random

import pyotp  # pyright: ignore[reportMissingImports]
import pw_hash as _pw  # pyright: ignore[reportMissingImports]

from _seed_helpers import (  # pyright: ignore[reportMissingImports]
    seed_fleet,
    seed_personal_minimums,
    seed_pilot_profiles,
    seed_rental_cycle,
    seed_reservations,
    seed_shared_ownership_on_existing_aircraft,
    seed_shared_ownership_tenant,
)
from models import (
    Role,
    Tenant,
    TenantUser,
    User,
    UserAircraftAccess,
    UserAllAircraftAccess,
    db,
)

# Fixed TOTP secret for the dev seed user — add this once to your
# authenticator app and it will always work across DB resets.
_DEV_TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # nosec B105  # intentional dev-only constant

_USERS = [
    # (email, password, role, language, name)
    ("admin@openhangar.dev", "openhangar-dev-1", Role.ADMIN, None, "Alex Admin"),
    ("pierre@openhangar.dev", "openhangar-dev-2", Role.VIEWER, "fr", "Pierre Dupont"),
    ("pilot@openhangar.dev", "openhangar-dev-3", Role.PILOT, None, "Sam Pilot"),
    (
        "maintenance@openhangar.dev",
        "openhangar-dev-4",
        Role.MAINTENANCE,
        None,
        "Max Mechanic",
    ),
    (
        "renter@openhangar.dev",
        "openhangar-dev-5",
        Role.PILOT,
        None,
        "Rita Renter",
    ),
]

# Co-owners of the dedicated OO-SH1 shared-ownership tenant (own tenant,
# separate from Dev Hangar above — mirrors demo_seed's per-slot Alice/Bob/
# Carol so demo seeding stays a straight multiplication of this).
# (email, password, name)
_SHARED_OWNERSHIP_USERS = [
    ("alice@openhangar.dev", "openhangar-dev-6", "Alice Owner"),
    ("bob@openhangar.dev", "openhangar-dev-7", "Bob Owner"),
    ("carol@openhangar.dev", "openhangar-dev-8", "Carol Owner"),
]


def seed() -> None:
    import os as _os

    _env = _os.environ.get("OPENHANGAR_ENV", "production")
    if _env != "development":
        raise RuntimeError(
            f"dev_seed.seed() must not be called in {_env!r} environment. "
            "Set OPENHANGAR_ENV=development."
        )
    # ── Tenant & users ────────────────────────────────────────────────────────
    tenant = Tenant(name="Dev Hangar", slug="dev-hangar")
    db.session.add(tenant)
    db.session.flush()

    admin_user = None
    pilot_user = None
    maintenance_user = None
    viewer_user = None
    renter_user = None
    for email, password, role, language, name in _USERS:
        is_admin = role == Role.ADMIN
        u = User(
            email=email,
            password_hash=_pw.hash(password),
            totp_secret=_DEV_TOTP_SECRET if is_admin else None,
            is_active=True,
            is_instance_admin=is_admin,
            name=name,
            **({"language": language} if language else {}),
        )
        db.session.add(u)
        db.session.flush()
        db.session.add(TenantUser(user_id=u.id, tenant_id=tenant.id, role=role))
        if is_admin:
            admin_user = u
        if role == Role.PILOT and pilot_user is None:
            pilot_user = u
            u.is_pilot = True
        elif role == Role.PILOT:
            renter_user = u
            u.is_pilot = True
        if role == Role.MAINTENANCE:
            maintenance_user = u
            u.is_maintenance = True
        if role == Role.VIEWER:
            viewer_user = u

    # ── Fleet (shared with demo seed) ─────────────────────────────────────────
    aircraft = seed_fleet(tenant.id)
    # aircraft order: [c172, seminole, robin, jodel]
    c172, seminole, robin, jodel = aircraft

    # ── Per-aircraft access for non-owner roles ───────────────────────────────
    # Admin gets all-planes access (demonstrates the UserAllAircraftAccess path)
    if admin_user:
        db.session.add(
            UserAllAircraftAccess(user_id=admin_user.id, tenant_id=tenant.id)
        )
    if pilot_user:
        db.session.add(UserAircraftAccess(user_id=pilot_user.id, aircraft_id=c172.id))
        db.session.add(
            UserAircraftAccess(user_id=pilot_user.id, aircraft_id=seminole.id)
        )
    if maintenance_user:
        db.session.add(
            UserAircraftAccess(user_id=maintenance_user.id, aircraft_id=robin.id)
        )
        db.session.add(
            UserAircraftAccess(user_id=maintenance_user.id, aircraft_id=jodel.id)
        )
    if viewer_user:
        db.session.add(UserAircraftAccess(user_id=viewer_user.id, aircraft_id=c172.id))
    if renter_user:
        db.session.add(UserAircraftAccess(user_id=renter_user.id, aircraft_id=c172.id))
        db.session.add(
            UserAircraftAccess(user_id=renter_user.id, aircraft_id=seminole.id)
        )

    # ── Reservations ─────────────────────────────────────────────────────────
    assert admin_user is not None  # nosec B101  # mypy narrowing invariant
    _res_pilots = [admin_user.id] + ([pilot_user.id] if pilot_user else [])
    seed_reservations(aircraft, _res_pilots)

    # ── Rental cycle (Phase 37): authorizations, dispatch, charges, downtime ──
    if pilot_user and renter_user:
        seed_rental_cycle(
            tenant.id,
            aircraft,
            owner_user_id=admin_user.id,
            renter_user_id=renter_user.id,
            expired_renter_user_id=pilot_user.id,
        )

    # ── Shared ownership (Phase 39), on the Robin — legacy escape-hatch only,
    # no TenantProfile change, so no other seeded page is affected ──────────
    # Deliberately no UserAircraftAccess grant for pilot_user here: co-owner
    # access to the owner-facing pages (my_share) is checked directly against
    # AircraftOwner, not user_can_access_aircraft, and Robin must stay
    # unreachable to pilot_user on aircraft-assignment-gated pages (e.g.
    # /flights) — see test_access_control.py's
    # test_pilot_cannot_access_unassigned_aircraft, which a UserAircraftAccess
    # grant here used to break.
    if pilot_user and maintenance_user:
        seed_shared_ownership_on_existing_aircraft(
            tenant.id,
            robin,
            [admin_user.id, pilot_user.id, maintenance_user.id],
        )

    # ── Shared-ownership tenant (own OO-SH1 aircraft, 3 co-owners) ────────────
    # Same dedicated-tenant approach demo_seed uses for its per-slot shared-
    # ownership sub-tenant — dev_seed defines it once here, demo multiplies it.
    sho_tenant = Tenant(name="Dev Hangar — Shared Ownership", slug="dev-hangar-sho")
    db.session.add(sho_tenant)
    db.session.flush()
    sho_user_ids: list[int] = []
    for email, password, name in _SHARED_OWNERSHIP_USERS:
        sho_user = User(
            email=email,
            password_hash=_pw.hash(password),
            is_active=True,
            name=name,
        )
        sho_user.is_pilot = True
        db.session.add(sho_user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=sho_user.id, tenant_id=sho_tenant.id, role=Role.OWNER)
        )
        sho_user_ids.append(sho_user.id)
    seed_shared_ownership_tenant(sho_tenant.id, sho_user_ids)

    # ── Pilot profile + sample logbook ────────────────────────────────────────
    seed_pilot_profiles(admin_user.id)
    seed_personal_minimums(admin_user.id)
    if pilot_user:
        seed_pilot_profiles(
            pilot_user.id,
            date_offset_days=lambda: random.randint(1, 4),  # nosec B311  # seed data, not security-sensitive
            license_number="BE.PPL(A).20387",
        )

    db.session.commit()

    # ── Log credentials ───────────────────────────────────────────────────────
    admin_email, _, _, _, _ = _USERS[0]
    totp_uri = pyotp.TOTP(_DEV_TOTP_SECRET).provisioning_uri(
        name=admin_email, issuer_name="OpenHangar"
    )

    role_width = max(
        max(len(r.value) for _, _, r, _, _ in _USERS), len(Role.OWNER.value)
    )
    print("=" * 60)
    print("  DEV SEED CREDENTIALS")
    print(f"  TOTP key : {_DEV_TOTP_SECRET}  (admin only)")
    print(f"  TOTP URI : {totp_uri}")
    print("-" * 60)
    for email, password, role, *_ in _USERS:
        print(f"  {role.value:<{role_width}}  {email}  /  {password}")
    print("-" * 60)
    print("  Shared-ownership co-owners (Dev Hangar — Shared Ownership / OO-SH1):")
    for email, password, _name in _SHARED_OWNERSHIP_USERS:
        print(f"  {Role.OWNER.value:<{role_width}}  {email}  /  {password}")
    print("-" * 60)
    print(f"  Aircraft seeded : {len(aircraft) + 1}")
    print("=" * 60)
