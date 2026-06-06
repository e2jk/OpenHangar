"""
Shared fixtures for Playwright end-to-end tests.

Run with:  pytest --e2e tests/e2e/ --override-ini='addopts='
Skip:      pytest tests/               (no --e2e → e2e tests are skipped)

The live server is seeded with the standard dev seed (dev_seed.py / _seed_helpers.py)
so that test data stays in sync with the development environment automatically.
E2E-specific extras (deletable flights, duplicate-detection anchor) are added on
top after the dev seed runs, using the same seeded aircraft.

Adding data needed for a new E2E test:
  1. If the data is representative of a real use case, add it to _seed_helpers.py
     so the dev environment benefits as well.
  2. If it is destructive (will be deleted by the test) or purely synthetic, add
     it in the "E2E-only extras" block below.
"""

import datetime
import os
import shutil
import socket
import tempfile
import threading
import time

import pytest

os.environ.setdefault("SECRET_KEY", "e2e-test-secret-not-for-production")
# dev_seed.seed() refuses to run unless FLASK_ENV=development
os.environ["FLASK_ENV"] = "development"


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
    """Start a live Flask server seeded with the standard dev dataset; yield (base_url, app)."""
    from sqlalchemy.pool import NullPool
    from init import create_app

    # Import dev seed artefacts for credentials and TOTP secret
    from dev_seed import _DEV_TOTP_SECRET, _USERS
    from dev_seed import seed as _dev_seed

    from models import (
        Aircraft,
        AircraftPhoto,
        Component,
        Document,
        Expense,
        FlightEntry,
        MaintenanceTrigger,
        PasswordResetToken,
        PilotLogbookEntry,
        Reservation,
        Role,
        ShareToken,
        Snag,
        Tenant,
        User,
        UserInvitation,
        WeightBalanceConfig,
        WeightBalanceEntry,
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

        # ── Run the standard dev seed ─────────────────────────────────────────
        _dev_seed()

        # ── Resolve seeded objects ────────────────────────────────────────────
        admin_email = _USERS[0][0]  # "admin@openhangar.dev"
        pilot_email = _USERS[2][0]  # "pilot@openhangar.dev"

        admin = User.query.filter_by(email=admin_email).first()
        pilot_user = User.query.filter_by(email=pilot_email).first()
        tenant = Tenant.query.filter_by(name="Dev Hangar").first()

        c172 = Aircraft.query.filter_by(registration="OO-PNH").first()
        seminole = Aircraft.query.filter_by(registration="OO-ABC").first()
        robin = Aircraft.query.filter_by(registration="OO-GRN").first()
        jodel = Aircraft.query.filter_by(registration="OO-TCH").first()

        # Most-recent c172 flight → first row in the flight list (sorted date desc)
        fe_flt = (
            FlightEntry.query.filter_by(aircraft_id=c172.id)
            .order_by(FlightEntry.date.desc())
            .first()
        )

        # Reference flight for duplicate-detection test: first jodel entry by date
        dup_ref = (
            FlightEntry.query.filter_by(aircraft_id=jodel.id)
            .order_by(FlightEntry.date)
            .first()
        )

        # ── Extra IDs for crawl test ──────────────────────────────────────────
        _comp = Component.query.filter_by(aircraft_id=c172.id).first()
        _photo = AircraftPhoto.query.filter_by(aircraft_id=c172.id).first()
        _doc_ac = Document.query.filter_by(aircraft_id=c172.id).first()
        _doc_pilot = Document.query.filter(Document.pilot_user_id.isnot(None)).first()
        _expense = Expense.query.filter_by(aircraft_id=c172.id).first()
        _snag = Snag.query.filter_by(aircraft_id=c172.id).first()
        _trigger = MaintenanceTrigger.query.filter_by(aircraft_id=c172.id).first()
        _wb_cfg = WeightBalanceConfig.query.filter_by(aircraft_id=c172.id).first()
        _wb_entry = (
            WeightBalanceEntry.query.filter_by(config_id=_wb_cfg.id).first()
            if _wb_cfg
            else None
        )
        _res = Reservation.query.filter_by(aircraft_id=c172.id).first()
        _share = ShareToken.query.filter_by(aircraft_id=c172.id).first()
        _pilot_entry = PilotLogbookEntry.query.first()

        # ── E2E-only extras: deletable flights ────────────────────────────────
        # Far-future dates ensure these rows appear first in the list so the
        # delete tests always click the right button.
        future = datetime.date.today() + datetime.timedelta(days=365)
        fe_del1 = FlightEntry(  # on robin — only used by cancel-delete test
            aircraft_id=robin.id,
            date=future,
            departure_icao="EBOS",
            arrival_icao="EBBR",
        )
        fe_del2 = FlightEntry(  # on seminole — used by accept-delete test
            aircraft_id=seminole.id,
            date=future,
            departure_icao="EBOS",
            arrival_icao="EBBR",
        )
        db.session.add_all([fe_del1, fe_del2])

        # ── E2E-only extras: token-based routes for crawl coverage ────────────
        import datetime as _dt
        from datetime import timezone as _tz

        far_future = _dt.datetime.now(_tz.utc) + _dt.timedelta(days=3650)

        _invite = UserInvitation(
            token="e2e-crawl-invite-token",
            tenant_id=tenant.id,
            invited_by_user_id=admin.id,
            email="crawl-invite@example.com",
            role=Role.PILOT,
            expires_at=far_future,
        )
        _reset = PasswordResetToken(
            token="e2e-crawl-reset-token",
            user_id=admin.id,
            generated_by_user_id=admin.id,
            expires_at=far_future,
        )
        db.session.add_all([_invite, _reset])

        db.session.flush()

        db.session.commit()

        SEED.update(
            {
                "admin_id": admin.id,
                "tenant_id": tenant.id,
                # Aircraft — mapped to the standard fleet
                "ac_flt": c172.id,  # clickable-row + GPS + logbook-toggle tests
                "ac_stop": seminole.id,  # action-cell test
                "ac_del1": robin.id,  # cancel-delete test
                "ac_del2": seminole.id,  # accept-delete test
                "ac_gps": c172.id,  # GPS upload / airport autocomplete tests
                "ac_dup": jodel.id,  # duplicate-banner test
                # Flights
                "fe_flt": fe_flt.id,
                "fe_del1": fe_del1.id,
                "fe_del2": fe_del2.id,
                # Duplicate-detection anchor (read from existing seeded data)
                "dup_date": dup_ref.date.isoformat(),
                "dup_dep": dup_ref.departure_icao,
                "dup_arr": dup_ref.arrival_icao,
                # Users
                "pilot_id": pilot_user.id,
                "user_id": admin.id,
                "totp_secret": _DEV_TOTP_SECRET,
                # Extra IDs for the crawl test (None → route skipped by test)
                "component_id": _comp.id if _comp else None,
                "photo_id": _photo.id if _photo else None,
                "document_id_ac": _doc_ac.id if _doc_ac else None,
                "document_id_pilot": _doc_pilot.id if _doc_pilot else None,
                "expense_id": _expense.id if _expense else None,
                "snag_id": _snag.id if _snag else None,
                "trigger_id": _trigger.id if _trigger else None,
                "wb_entry_id": _wb_entry.id if _wb_entry else None,
                "res_id": _res.id if _res else None,
                "token_id": _share.id if _share else None,
                "share_token": _share.token if _share else None,
                "reset_token": "e2e-crawl-reset-token",
                "invite_token": "e2e-crawl-invite-token",
                "pilot_entry_id": _pilot_entry.id if _pilot_entry else None,
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

    The dev-seed admin has TOTP enabled (JBSWY3DPEHPK3PXP). The TOTP
    auto-submit JS handles form submission; we just wait for navigation away
    from /login after filling the code.
    """
    import pyotp

    page.goto(f"{live_server_url}/login")
    page.wait_for_load_state("networkidle")
    if "/login" in page.url:
        page.fill('input[name="email"]', "admin@openhangar.dev")
        page.fill('input[name="password"]', "openhangar-dev-1")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
        # TOTP step — admin has TOTP enabled in the dev seed
        if page.locator("#totp_code").count() > 0:
            code = pyotp.TOTP(SEED["totp_secret"]).now()
            page.fill("#totp_code", code)
            # Auto-submit JS fires when the 6th digit is entered; fall back to an
            # explicit submit click if the JS hasn't triggered within 5 s (slow CI).
            try:
                page.wait_for_url(lambda url: "/login" not in url, timeout=5000)
            except Exception:
                page.locator('button[type="submit"]').click()
                page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
    return page
