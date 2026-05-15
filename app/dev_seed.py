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

import bcrypt  # pyright: ignore[reportMissingImports]
import pyotp  # pyright: ignore[reportMissingImports]

from _seed_helpers import seed_fleet, seed_pilot_profiles, seed_reservations  # pyright: ignore[reportMissingImports]
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
]


def seed() -> None:
    # ── Tenant & users ────────────────────────────────────────────────────────
    tenant = Tenant(name="Dev Hangar")
    db.session.add(tenant)
    db.session.flush()

    admin_user = None
    pilot_user = None
    maintenance_user = None
    viewer_user = None
    for email, password, role, language, name in _USERS:
        is_admin = role == Role.ADMIN
        u = User(
            email=email,
            password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
            totp_secret=_DEV_TOTP_SECRET if is_admin else None,
            is_active=True,
            name=name,
            **({"language": language} if language else {}),
        )
        db.session.add(u)
        db.session.flush()
        db.session.add(TenantUser(user_id=u.id, tenant_id=tenant.id, role=role))
        if is_admin:
            admin_user = u
        if role == Role.PILOT:
            pilot_user = u
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

    # ── Reservations ─────────────────────────────────────────────────────────
    assert admin_user is not None  # nosec B101  # mypy narrowing invariant
    _res_pilots = [admin_user.id] + ([pilot_user.id] if pilot_user else [])
    seed_reservations(aircraft, _res_pilots)

    # ── Pilot profile + sample logbook ────────────────────────────────────────
    seed_pilot_profiles(admin_user.id)
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

    role_width = max(len(r.value) for _, _, r, _, _ in _USERS)
    print("=" * 60)
    print("  DEV SEED CREDENTIALS")
    print(f"  TOTP key : {_DEV_TOTP_SECRET}  (admin only)")
    print(f"  TOTP URI : {totp_uri}")
    print("-" * 60)
    for email, password, role, *_ in _USERS:
        print(f"  {role.value:<{role_width}}  {email}  /  {password}")
    print("-" * 60)
    print(f"  Aircraft seeded : {len(aircraft)}")
    print("=" * 60)
