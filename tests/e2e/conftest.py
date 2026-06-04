"""
Shared fixtures for Playwright end-to-end tests.

Run with:  pytest --e2e tests/e2e/ --override-ini='addopts='
Skip:      pytest tests/               (no --e2e → e2e tests are skipped)

All test data is pre-seeded in the live_server fixture before the Flask server
starts, avoiding cross-thread SQLAlchemy session visibility issues with SQLite.
"""

import os
import shutil
import socket
import tempfile
import threading
import time

import bcrypt
import pytest

os.environ.setdefault("SECRET_KEY", "e2e-test-secret-not-for-production")

# Fixed TOTP secret — lets tests generate valid codes with pyotp
TOTP_SECRET = "JBSWY3DPEHPK3PXP"


# ── Skip guard ─────────────────────────────────────────────────────────────────


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--e2e"):
        skip = pytest.mark.skip(reason="pass --e2e to run browser tests")
        for item in items:
            if item.get_closest_marker("e2e"):
                item.add_marker(skip)


# ── Seed data IDs (populated by live_server, read by tests) ───────────────────

SEED: dict = {}  # populated at session start


# ── Live server ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def live_server():
    """Start a live Flask server with pre-seeded data; yield (base_url, app)."""
    import datetime
    from sqlalchemy.pool import NullPool
    from init import create_app
    from models import (
        Aircraft,
        FlightEntry,
        PilotLogbookEntry,
        Role,
        Tenant,
        TenantUser,
        User,
        db,
    )

    upload_dir = tempfile.mkdtemp()
    db_file = os.path.join(upload_dir, "e2e_test.db")
    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        # Disable Secure cookie flag so the browser sends it over http:// in E2E tests
        SESSION_COOKIE_SECURE=False,
        # File-based SQLite + NullPool: each request opens a fresh connection,
        # ensuring data committed before server start is always visible.
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_file}",
        SQLALCHEMY_ENGINE_OPTIONS={
            "connect_args": {"check_same_thread": False},
            "poolclass": NullPool,
        },
        UPLOAD_FOLDER=upload_dir,
        SERVER_NAME=None,
    )

    with app.app_context():
        db.create_all()

        # Admin user + tenant
        tenant = Tenant(name="E2E Hangar", is_active=True)
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email="admin@e2e.test",
            password_hash=bcrypt.hashpw(b"E2ePassword1!", bcrypt.gensalt(4)).decode(),
            totp_secret=None,
            is_active=True,
            is_instance_admin=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )

        # Aircraft + flights for interaction tests
        ac_flt = Aircraft(
            registration="E2E-FLT", make="Cessna", model="172", tenant_id=tenant.id
        )
        ac_stop = Aircraft(
            registration="E2E-STOP", make="Cessna", model="172", tenant_id=tenant.id
        )
        ac_del1 = Aircraft(
            registration="E2E-DEL1", make="Cessna", model="172", tenant_id=tenant.id
        )
        ac_del2 = Aircraft(
            registration="E2E-DEL2", make="Cessna", model="172", tenant_id=tenant.id
        )
        ac_gps = Aircraft(
            registration="E2E-GPS", make="Cessna", model="172", tenant_id=tenant.id
        )
        ac_dup = Aircraft(
            registration="E2E-DUP", make="Cessna", model="172", tenant_id=tenant.id
        )
        for ac in (ac_flt, ac_stop, ac_del1, ac_del2, ac_gps, ac_dup):
            db.session.add(ac)
        db.session.flush()

        fe_flt = FlightEntry(
            aircraft_id=ac_flt.id,
            date=datetime.date(2024, 1, 15),
            departure_icao="EBBR",
            arrival_icao="LFPG",
        )
        fe_stop = FlightEntry(
            aircraft_id=ac_stop.id,
            date=datetime.date(2024, 2, 1),
            departure_icao="EBBR",
            arrival_icao="LFPG",
        )
        fe_del1 = FlightEntry(
            aircraft_id=ac_del1.id,
            date=datetime.date(2024, 3, 1),
            departure_icao="EBBR",
            arrival_icao="LFPG",
        )
        fe_del2 = FlightEntry(
            aircraft_id=ac_del2.id,
            date=datetime.date(2024, 4, 1),
            departure_icao="EBBR",
            arrival_icao="LFPG",
        )
        # Pre-existing entry used by the duplicate-banner E2E test
        fe_dup = FlightEntry(
            aircraft_id=ac_dup.id,
            date=datetime.date(2024, 5, 10),
            departure_icao="EBOS",
            arrival_icao="EBBR",
        )
        for fe in (fe_flt, fe_stop, fe_del1, fe_del2, fe_dup):
            db.session.add(fe)

        # Pilot user — provides a second row on /config/users/ for role-select test
        pilot = User(
            email="pilot@e2e.test",
            password_hash=bcrypt.hashpw(b"pass", bcrypt.gensalt(4)).decode(),
            is_active=True,
        )
        db.session.add(pilot)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=pilot.id, tenant_id=tenant.id, role=Role.PILOT)
        )

        # Pilot logbook entry for registration-lookup test
        db.session.add(
            PilotLogbookEntry(
                pilot_user_id=pilot.id,
                date=datetime.date(2024, 1, 10),
                aircraft_registration="E2E-LOOKUP",
                aircraft_type="Cessna 172",
            )
        )

        # User with TOTP enabled — for TOTP auto-submit test
        totp_user = User(
            email="totp@e2e.test",
            password_hash=bcrypt.hashpw(b"TotpPass1!", bcrypt.gensalt(4)).decode(),
            totp_secret=TOTP_SECRET,
            is_active=True,
        )
        db.session.add(totp_user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=totp_user.id, tenant_id=tenant.id, role=Role.PILOT)
        )

        db.session.commit()

        # Store IDs for use in tests
        SEED.update(
            {
                "admin_id": user.id,
                "tenant_id": tenant.id,
                "ac_flt": ac_flt.id,
                "ac_stop": ac_stop.id,
                "ac_del1": ac_del1.id,
                "ac_del2": ac_del2.id,
                "ac_gps": ac_gps.id,
                "ac_dup": ac_dup.id,
                "fe_flt": fe_flt.id,
                "fe_stop": fe_stop.id,
                "fe_del1": fe_del1.id,
                "fe_del2": fe_del2.id,
                "fe_dup": fe_dup.id,
                "pilot_id": pilot.id,
                "totp_user_id": totp_user.id,
                "totp_secret": TOTP_SECRET,
            }
        )

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    t = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, use_reloader=False),
        daemon=True,
    )
    t.start()
    time.sleep(0.8)

    yield f"http://127.0.0.1:{port}", app, SEED

    shutil.rmtree(upload_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def live_server_url(live_server):
    url, _, _ = live_server
    return url


@pytest.fixture(scope="session")
def live_app(live_server):
    _, app, _ = live_server
    return app


@pytest.fixture(scope="session")
def seed(live_server):
    """Seeded test data IDs; safe to use after live_server has populated SEED."""
    _, _, seed_dict = live_server
    return seed_dict


# ── Playwright fixtures ────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def browser_context(
    live_server_url,
):  # depends on live_server_url to ensure server is up first
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(base_url=live_server_url)
        yield context
        browser.close()


@pytest.fixture
def page(browser_context):
    """Fresh page per test."""
    pg = browser_context.new_page()
    yield pg
    pg.close()


@pytest.fixture
def unauthenticated_page(browser_context, live_server_url):
    """Fresh page in a brand-new browser context — no inherited session cookies.
    Used for tests that need to exercise the full login flow from scratch."""
    fresh_ctx = browser_context.browser.new_context(base_url=live_server_url)
    pg = fresh_ctx.new_page()
    yield pg
    pg.close()
    fresh_ctx.close()


@pytest.fixture
def logged_in_page(page, live_server_url):
    """Page authenticated as the seeded admin.
    Since browser_context is session-scoped, the auth cookie persists after
    the first login. We navigate to /login: if already authenticated Flask
    redirects to /, so we only fill the form when the login page is shown.
    """
    page.goto(f"{live_server_url}/login")
    page.wait_for_load_state("networkidle")
    if "/login" in page.url:
        page.fill('input[name="email"]', "admin@e2e.test")
        page.fill('input[name="password"]', "E2ePassword1!")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
    return page
