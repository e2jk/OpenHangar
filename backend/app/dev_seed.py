"""
Development seed data.
Creates a default admin user + tenant and a sample fleet so the app is
usable immediately after a fresh dev install without going through the
setup wizard.

Each phase of the implementation plan adds to this seed so the dev
environment always reflects the full feature set built so far.

Credentials printed to container logs on first run.
Never loaded in production.
"""

import bcrypt # pyright: ignore[reportMissingImports]
import pyotp # pyright: ignore[reportMissingImports]
from datetime import date

from models import Aircraft, Component, ComponentType, Role, Tenant, TenantUser, User, db

# Fixed TOTP secret for the dev seed user — add this once to your
# authenticator app and it will always work across DB resets.
_DEV_TOTP_SECRET = "JBSWY3DPEHPK3PXP"
_DEV_EMAIL = "admin@openhangar.dev"
_DEV_PASSWORD = "openhangar-dev-1"


def seed():
    # ── Tenant & user ─────────────────────────────────────────────────────────
    tenant = Tenant(name="Dev Hangar")
    db.session.add(tenant)
    db.session.flush()

    user = User(
        email=_DEV_EMAIL,
        password_hash=bcrypt.hashpw(_DEV_PASSWORD.encode(), bcrypt.gensalt()).decode(),
        totp_secret=_DEV_TOTP_SECRET,
        is_active=True,
    )
    db.session.add(user)
    db.session.flush()

    db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN))

    # ── Phase 1: sample fleet ─────────────────────────────────────────────────

    # Single-engine: Cessna 172S
    c172 = Aircraft(
        tenant_id=tenant.id,
        registration="OO-PNH",
        make="Cessna",
        model="172S Skyhawk",
        year=2004,
    )
    db.session.add(c172)
    db.session.flush()

    db.session.add(Component(
        aircraft_id=c172.id,
        type=ComponentType.ENGINE,
        make="Lycoming",
        model="IO-360-L2A",
        serial_number="L-23456-51A",
        time_at_install=312.0,
        installed_at=date(2019, 6, 15),
        extras={"tbo_hours": 2000},
    ))
    db.session.add(Component(
        aircraft_id=c172.id,
        type=ComponentType.PROPELLER,
        make="McCauley",
        model="1C160/DTM7557",
        serial_number="MC-880342",
        time_at_install=312.0,
        installed_at=date(2019, 6, 15),
        extras={"blade_count": 2, "diameter_in": 76},
    ))

    # Multi-engine: Piper PA-44 Seminole
    seminole = Aircraft(
        tenant_id=tenant.id,
        registration="OO-ABC",
        make="Piper",
        model="PA-44-180 Seminole",
        year=1998,
    )
    db.session.add(seminole)
    db.session.flush()

    for position, model, serial in [
        ("left",  "O-360-E1A6D",  "L-54321-27A"),
        ("right", "LO-360-E1A6D", "L-54322-27A"),
    ]:
        db.session.add(Component(
            aircraft_id=seminole.id,
            type=ComponentType.ENGINE,
            position=position,
            make="Lycoming",
            model=model,
            serial_number=serial,
            time_at_install=780.0,
            installed_at=date(2015, 3, 1),
            extras={"tbo_hours": 2000},
        ))

    for position, serial in [("left", "P-11001"), ("right", "P-11002")]:
        db.session.add(Component(
            aircraft_id=seminole.id,
            type=ComponentType.PROPELLER,
            position=position,
            make="Hartzell",
            model="HC-C2YK-1BF",
            serial_number=serial,
            time_at_install=780.0,
            installed_at=date(2015, 3, 1),
            extras={"blade_count": 2, "variable_pitch": True},
        ))

    db.session.commit()

    # ── Log credentials ───────────────────────────────────────────────────────
    totp_uri = pyotp.TOTP(_DEV_TOTP_SECRET).provisioning_uri(
        name=_DEV_EMAIL, issuer_name="OpenHangar"
    )

    print("=" * 60)
    print("  DEV SEED CREDENTIALS")
    print(f"  Email    : {_DEV_EMAIL}")
    print(f"  Password : {_DEV_PASSWORD}")
    print(f"  TOTP key : {_DEV_TOTP_SECRET}")
    print(f"  TOTP URI : {totp_uri}")
    print("=" * 60)
    print("  SAMPLE FLEET")
    print("  OO-PNH  Cessna 172S (single-engine)")
    print("  OO-ABC  Piper PA-44 Seminole (twin-engine)")
    print("=" * 60)
