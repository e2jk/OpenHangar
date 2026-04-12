"""
Development seed data.
Creates a default admin user + tenant so the app is usable immediately
after a fresh dev install without going through the setup wizard.

Credentials printed to container logs on first run.
Never loaded in production.
"""

import bcrypt
import pyotp

from models import Role, Tenant, TenantUser, User, db

# Fixed TOTP secret for the dev seed user — add this once to your
# authenticator app and it will always work across DB resets.
_DEV_TOTP_SECRET = "JBSWY3DPEHPK3PXP"
_DEV_EMAIL = "admin@openhangar.dev"
_DEV_PASSWORD = "openhangar-dev-1"


def seed():
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
    db.session.commit()

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
