"""
Development seed data.
Creates a default admin user + tenant and a sample fleet so the app is
usable immediately after a fresh dev install without going through the
setup wizard.

Fleet content lives in _seed_helpers.py and is shared with demo_seed.
Credentials printed to container logs on first run.
Never loaded in production.
"""

import bcrypt  # pyright: ignore[reportMissingImports]
import pyotp   # pyright: ignore[reportMissingImports]

from _seed_helpers import seed_fleet, seed_pilot_profiles  # pyright: ignore[reportMissingImports]
from models import Role, Tenant, TenantUser, User, db

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

    # ── Second user: French locale (exercises language preference in dev) ──────
    fr_user = User(
        email="pierre@openhangar.dev",
        password_hash=bcrypt.hashpw(b"openhangar-dev-2", bcrypt.gensalt()).decode(),
        is_active=True,
        language="fr",
    )
    db.session.add(fr_user)
    db.session.flush()
    db.session.add(TenantUser(user_id=fr_user.id, tenant_id=tenant.id, role=Role.VIEWER))

    # ── Fleet (shared with demo seed) ─────────────────────────────────────────
    seed_fleet(tenant.id)

    # ── Pilot profile + sample logbook ────────────────────────────────────────
    seed_pilot_profiles(user.id)

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
    print("  OO-PNH  Cessna 172S         — status: OVERDUE  (7 flights, 3 mx)")
    print("  OO-ABC  Piper PA-44         — status: DUE SOON (4 flights, 3 mx)")
    print("  OO-GRN  Robin DR-401/155CDI — status: OK       (2 flights, 2 mx)")
    print("=" * 60)
