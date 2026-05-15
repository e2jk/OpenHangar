"""
Demo seed — creates N isolated visitor slots (default: 20).
Each slot is an independent tenant + four users (owner, pilot, maintenance,
viewer) + fleet, populated via the shared seed_fleet() helper.
Called by docker-init-db.py at container startup (always wipes first)
and by `flask seed-demo` from the refresh cron script.
"""

import os
import random

import bcrypt  # pyright: ignore[reportMissingImports]

from _seed_helpers import seed_fleet, seed_pilot_profiles, seed_reservations  # pyright: ignore[reportMissingImports]
from models import DemoSlot, Role, Tenant, TenantUser, User, UserAllAircraftAccess, db


def _slot_count() -> int:
    try:
        return int(os.environ.get("DEMO_SLOT_COUNT", "20"))
    except ValueError:
        return 20


def seed() -> None:
    """Wipe all demo tenants/users and recreate N fresh slots."""
    existing = DemoSlot.query.all()
    for slot in existing:
        tenant = db.session.get(Tenant, slot.tenant_id)
        for uid in [
            slot.user_id,
            slot.renter_user_id,
            slot.maintenance_user_id,
            slot.viewer_user_id,
        ]:
            if uid:
                user = db.session.get(User, uid)
                if user:
                    db.session.delete(user)
        db.session.delete(slot)
        if tenant:
            db.session.delete(tenant)
    db.session.flush()

    n = _slot_count()
    dummy_hash = bcrypt.hashpw(b"demo-slot-password", bcrypt.gensalt()).decode()

    used_display_ids: set[int] = set()

    for i in range(1, n + 1):
        display_id = random.randint(1000, 9999)  # nosec B311  # cosmetic ID, not security-sensitive
        while display_id in used_display_ids:
            display_id = random.randint(1000, 9999)  # nosec B311
        used_display_ids.add(display_id)

        tenant = Tenant(name=f"Demo Hangar #{display_id}")
        db.session.add(tenant)
        db.session.flush()

        def _make_user(email: str, name: str, role: Role) -> User:
            u = User(
                email=email,
                password_hash=dummy_hash,
                totp_secret=None,
                is_active=True,
                name=name,
            )
            db.session.add(u)
            db.session.flush()
            db.session.add(TenantUser(user_id=u.id, tenant_id=tenant.id, role=role))
            return u

        owner_user = _make_user(
            f"demo-owner-{i}@openhangar.demo", "Demo Owner", Role.OWNER
        )

        pilot_user = _make_user(
            f"demo-pilot-{i}@openhangar.demo", "Demo Pilot", Role.PILOT
        )
        pilot_user.is_pilot = True

        maint_user = _make_user(
            f"demo-maintenance-{i}@openhangar.demo", "Demo Mechanic", Role.MAINTENANCE
        )
        maint_user.is_maintenance = True

        viewer_user = _make_user(
            f"demo-viewer-{i}@openhangar.demo", "Demo Viewer", Role.VIEWER
        )

        # All non-owner users get all-planes access so every role can explore the full fleet
        for u in (pilot_user, maint_user, viewer_user):
            db.session.add(UserAllAircraftAccess(user_id=u.id, tenant_id=tenant.id))

        aircraft = seed_fleet(tenant.id)
        seed_reservations(aircraft, [owner_user.id, pilot_user.id])
        seed_pilot_profiles(owner_user.id)
        seed_pilot_profiles(pilot_user.id)

        db.session.add(
            DemoSlot(
                id=i,
                display_id=display_id,
                tenant_id=tenant.id,
                user_id=owner_user.id,
                renter_user_id=pilot_user.id,
                maintenance_user_id=maint_user.id,
                viewer_user_id=viewer_user.id,
                last_activity_at=None,
            )
        )

    db.session.commit()
    print(
        f"Demo seed complete: {n} slots created (owner + pilot + maintenance + viewer per slot)."
    )
