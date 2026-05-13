"""
Demo seed — creates N isolated visitor slots (default: 20).
Each slot is an independent tenant + user + fleet, populated via the shared
seed_fleet() helper so the demo always reflects the latest dev content.
Called by docker-init-db.py at container startup (always wipes first)
and by `flask seed-demo` from the refresh cron script.
"""

import os
import random

import bcrypt  # pyright: ignore[reportMissingImports]

from _seed_helpers import seed_fleet, seed_pilot_profiles, seed_reservations  # pyright: ignore[reportMissingImports]
from models import DemoSlot, Role, Tenant, TenantUser, User, db


def _slot_count() -> int:
    try:
        return int(os.environ.get("DEMO_SLOT_COUNT", "20"))
    except ValueError:
        return 20


def seed() -> None:
    """Wipe all demo tenants/users and recreate N fresh slots."""
    # Remove existing demo slots (cascades to their tenants and users)
    existing = DemoSlot.query.all()
    for slot in existing:
        tenant = db.session.get(Tenant, slot.tenant_id)
        user = db.session.get(User, slot.user_id)
        db.session.delete(slot)
        if tenant:
            db.session.delete(tenant)
        if user:
            db.session.delete(user)
    db.session.flush()

    n = _slot_count()
    # Use a fixed dummy password hash — password is never surfaced to visitors
    dummy_hash = bcrypt.hashpw(b"demo-slot-password", bcrypt.gensalt()).decode()

    used_display_ids: set[int] = set()

    for i in range(1, n + 1):
        display_id = random.randint(1000, 9999)
        while display_id in used_display_ids:
            display_id = random.randint(1000, 9999)
        used_display_ids.add(display_id)

        tenant = Tenant(name=f"Demo Hangar #{display_id}")
        db.session.add(tenant)
        db.session.flush()

        user = User(
            email=f"demo-{i}@openhangar.demo",
            password_hash=dummy_hash,
            totp_secret=None,
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()

        db.session.add(TenantUser(
            user_id=user.id, tenant_id=tenant.id, role=Role.OWNER
        ))

        renter_user = User(
            email=f"demo-renter-{i}@openhangar.demo",
            password_hash=dummy_hash,
            totp_secret=None,
            is_active=True,
        )
        db.session.add(renter_user)
        db.session.flush()

        db.session.add(TenantUser(
            user_id=renter_user.id, tenant_id=tenant.id, role=Role.PILOT
        ))

        aircraft = seed_fleet(tenant.id)
        seed_reservations(aircraft, [user.id, renter_user.id])
        seed_pilot_profiles(user.id)

        db.session.add(DemoSlot(
            id=i,
            display_id=display_id,
            tenant_id=tenant.id,
            user_id=user.id,
            renter_user_id=renter_user.id,
            last_activity_at=None,
        ))

    db.session.commit()
    print(f"Demo seed complete: {n} slots created.")
