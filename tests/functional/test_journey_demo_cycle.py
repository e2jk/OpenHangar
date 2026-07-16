"""J15 — Demo cycle (docs/functional_test_plan.md).

Intent per the plan: enter demo -> make a change in slot 1 -> a second
visitor gets a different slot unaffected by slot 1's edits; demo data
never bleeds into a real tenant.

Documented deviation/clarification (the plan's own "deviate only with a
reason" rule): "demo data never bleeds into a real tenant" isn't a
same-database, filtered-by-flag guarantee that a functional test can
probe the way J11 probes cross-tenant isolation -- there is no
`Tenant.is_demo` column or similar; demo tenants are ordinary Tenant
rows isolated by the exact same tenant_id scoping every tenant uses.
The actual production guarantee is structural: the demo instance is a
separate deployment/database entirely, and demo_seed.seed()/the
reset-db CLI command both hard-refuse to run unless
OPENHANGAR_ENV=="demo" (app/demo_seed.py, app/init.py) -- the demo
blueprint itself isn't even registered outside demo mode (already
covered by test_demo.py). This journey asserts what's actually real and
new: (1) that guard raises RuntimeError outside demo mode, and (2) two
concurrent demo visitors land on different slots via the real
/demo/enter LRU-assignment path and see none of each other's writes --
ordinary tenant scoping is the enforcement mechanism, exercised here
through the demo-specific entry flow rather than a hand-built fixture,
which is what test_demo.py's existing single-client tests never do.

Own local demo_app/demo_client fixtures (function-scoped, mirroring
tests/test_demo.py's own demo_app pattern): the shared session-scoped
app from tests/conftest.py never sets OPENHANGAR_ENV=demo, so the demo
blueprint is never registered on it.

Existing: test_demo.py (759 lines) covers slot assignment, LRU
preference, restore/stale-slot paths, and role->user mapping
exhaustively, but always from one client's perspective, using
hand-built DemoSlot rows rather than the real seed() function, and
never asserting what a *second* visitor sees.
"""

import os
from datetime import datetime, timedelta, timezone

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]
from init import create_app  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    DemoSlot,
    Expense,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)
from sqlalchemy.pool import StaticPool  # pyright: ignore[reportMissingImports]


@pytest.fixture()
def demo_app():
    old = os.environ.get("OPENHANGAR_ENV")
    os.environ["OPENHANGAR_ENV"] = "demo"
    try:
        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.config["RATELIMIT_ENABLED"] = False
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        }
        with app.app_context():
            db.create_all()
        yield app
        with app.app_context():
            db.drop_all()
            db.engine.dispose()
    finally:
        if old is None:
            os.environ.pop("OPENHANGAR_ENV", None)
        else:
            os.environ["OPENHANGAR_ENV"] = old


def _make_demo_slot(app, slot_id, last_activity):
    """Create a tenant + owner + one aircraft for one demo slot.

    Direct writes throughout: this is seeding a demo sandbox, not driving
    a user-facing creation flow -- the plan's own convention for fixture
    setup that isn't the thing under test (the /demo/enter assignment
    path is).
    """
    with app.app_context():
        tenant = Tenant(name=f"Demo Hangar #{slot_id}")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=f"demo-{slot_id}@openhangar.demo",
            password_hash=_pw_hash.hash("x"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        aircraft = Aircraft(
            tenant_id=tenant.id,
            registration=f"OO-DM{slot_id}",
            make="Cessna",
            model="172S",
        )
        db.session.add(aircraft)
        db.session.flush()
        slot = DemoSlot(
            id=slot_id,
            tenant_id=tenant.id,
            user_id=user.id,
            last_activity_at=last_activity,
        )
        db.session.add(slot)
        db.session.commit()
        return aircraft.id


def test_demo_seed_refuses_outside_demo_env(demo_app):
    os.environ["OPENHANGAR_ENV"] = "production"
    try:
        import demo_seed  # pyright: ignore[reportMissingImports]

        with demo_app.app_context(), pytest.raises(RuntimeError):
            demo_seed.seed()
    finally:
        os.environ["OPENHANGAR_ENV"] = "demo"


def test_two_visitors_get_different_slots_unaffected_by_each_others_edits(demo_app):
    # Both timestamps are well past the default 30-minute busy window (so
    # neither entry 503s as "all slots busy"), and slot 1 is older than
    # slot 2 -- LRU-first order is deterministic: visitor A gets slot 1,
    # and once /demo/enter touches it to "now", slot 2 becomes the oldest
    # remaining, so visitor B is guaranteed slot 2 next.
    now = datetime.now(timezone.utc)
    aircraft_1 = _make_demo_slot(demo_app, 1, now - timedelta(hours=2))
    aircraft_2 = _make_demo_slot(demo_app, 2, now - timedelta(hours=1))

    visitor_a = demo_app.test_client()
    visitor_a.post("/demo/enter")
    with visitor_a.session_transaction() as sess:
        assert sess["demo_slot_id"] == 1

    # Visitor A makes a real write in their slot.
    resp = visitor_a.post(
        f"/aircraft/{aircraft_1}/expenses/add",
        data={"date": "2024-06-01", "expense_type": "fuel", "amount": "42.00"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with demo_app.app_context():
        assert Expense.query.filter_by(aircraft_id=aircraft_1).count() == 1

    # A second, independent visitor -- slot 1 is now the most-recently
    # touched, so LRU assignment guarantees slot 2 for visitor B.
    visitor_b = demo_app.test_client()
    visitor_b.post("/demo/enter")
    with visitor_b.session_transaction() as sess:
        assert sess["demo_slot_id"] == 2

    # Visitor B's own aircraft has no expenses -- slot A's write didn't bleed.
    with demo_app.app_context():
        assert Expense.query.filter_by(aircraft_id=aircraft_2).count() == 0

    # Visitor B directly requesting slot A's aircraft gets the same
    # tenant-scoped 404 any two unrelated tenants would -- ordinary
    # tenant isolation, exercised through the demo entry flow.
    cross_resp = visitor_b.get(f"/aircraft/{aircraft_1}")
    assert cross_resp.status_code == 404
