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

import contextlib
import datetime
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

os.environ.setdefault("OPENHANGAR_SECRET_KEY", "e2e-test-secret-not-for-production")
# dev_seed.seed() refuses to run unless FLASK_ENV=development
os.environ["OPENHANGAR_ENV"] = "development"

# When set, skip the in-process Flask server and run tests against this URL.
# Used for Docker-based E2E (CI) and optionally against the local dev server.
_E2E_BASE_URL = os.environ.get("E2E_BASE_URL")
_SEED_JSON = Path(__file__).parent / "seed.json"

# Destructive tests (flight deletion, role change) are skipped when running
# against an external server unless explicitly opted-in.  Set
# E2E_ALLOW_DESTRUCTIVE=1 in CI where the DB is disposable.
_E2E_ALLOW_DESTRUCTIVE = os.environ.get("E2E_ALLOW_DESTRUCTIVE", "0") == "1"

# Same idea as E2E_BASE_URL/E2E_ALLOW_DESTRUCTIVE above, but for the
# setup-flow tests specifically (see fresh_server below): the real Docker
# image, running in production mode against its own disposable, unseeded
# Postgres container — full production parity instead of the SQLite
# in-process fallback. E2E_SETUP_FLOW_DB_URL is a direct connection to that
# same Postgres, used to truncate all tables before each test function so
# state never leaks despite the whole CI job sharing one container.
_E2E_SETUP_FLOW_BASE_URL = os.environ.get("E2E_SETUP_FLOW_BASE_URL")
_E2E_SETUP_FLOW_DB_URL = os.environ.get("E2E_SETUP_FLOW_DB_URL")


def _serialize_wsgi_requests(app):
    """Force fully sequential request handling for a SQLite-backed in-process
    e2e server.

    app.run(..., use_reloader=False) doesn't pass threaded=True, which
    *should* already guarantee this — but this fixture's SQLite database has
    intermittently thrown `sqlite3.InterfaceError: bad parameter or other
    API misuse` under real browser load (multiple parallel requests, e.g.
    a page's several <link rel="prefetch"> tags firing at once), the classic
    symptom of two threads touching the same SQLite connection concurrently.
    Rather than depend on Werkzeug's dev-server internals never changing,
    wrap the WSGI app in an explicit lock so only one request is ever
    in-flight at a time, regardless of how the server dispatches connections.
    """
    _lock = threading.Lock()
    _wsgi_app = app.wsgi_app

    def _locked_wsgi_app(environ, start_response):
        with _lock:
            return _wsgi_app(environ, start_response)

    app.wsgi_app = _locked_wsgi_app


# ── Skip guard ─────────────────────────────────────────────────────────────────


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--e2e"):
        skip = pytest.mark.skip(reason="pass --e2e to run browser tests")
        for item in items:
            if item.get_closest_marker("e2e"):
                item.add_marker(skip)
    if _E2E_BASE_URL and not _E2E_ALLOW_DESTRUCTIVE:
        skip = pytest.mark.skip(
            reason="destructive: modifies server data — set E2E_ALLOW_DESTRUCTIVE=1 to enable"
        )
        for item in items:
            if item.get_closest_marker("destructive"):
                item.add_marker(skip)


# ── Seed data IDs (populated by live_server, read by tests) ───────────────────

SEED: dict = {}  # populated at session start


# ── Live server ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def live_server():
    """Start a live Flask server, or delegate to an external server when E2E_BASE_URL is set."""
    if _E2E_BASE_URL:
        # ── Docker / external-server mode ─────────────────────────────────────
        # The server is already running (Docker container in dev mode).
        # Seed IDs come from tests/e2e/seed.json written by generate_routes.py --seed-out.
        from dev_seed import _DEV_TOTP_SECRET

        samples: dict = (
            json.loads(_SEED_JSON.read_text()) if _SEED_JSON.exists() else {}
        )

        def _s(key: str, fallback_key: str | None = None):
            v = samples.get(key)
            return (
                v
                if v is not None
                else (samples.get(fallback_key) if fallback_key else None)
            )

        SEED.update(
            {
                "admin_id": _s("user_id"),
                "tenant_id": _s("tenant_id"),
                # Aircraft — ordered by ID: 1=c172, 2=seminole, 3=robin, 4=jodel
                "ac_flt": _s("aircraft_id"),
                "ac_flt_reg": _s("aircraft_registration"),
                "ac_stop": _s("aircraft_id_2", "aircraft_id"),
                "ac_del1": _s("aircraft_id_3", "aircraft_id"),
                "ac_del2": _s("aircraft_id_2", "aircraft_id"),
                "ac_gps": _s("aircraft_id"),
                "ac_dup": _s("aircraft_id_4", "aircraft_id"),
                # Flights
                "fe_flt": _s("flight_id"),
                "fe_del1": _s("flight_id_3"),  # robin flight — cancel-delete test
                "fe_del2": _s(
                    "flight_id_2"
                ),  # seminole flight — accept-delete + action-cell
                # Duplicate-detection anchor (first jodel flight)
                "dup_date": _s("dup_date"),
                "dup_dep": _s("dup_dep"),
                "dup_arr": _s("dup_arr"),
                # Users
                "pilot_id": _s("user_id"),
                "user_id": _s("user_id"),
                "totp_secret": _DEV_TOTP_SECRET,
                # Resource IDs for crawl and known-behavior tests
                "component_id": _s("component_id"),
                "photo_id": _s("photo_id"),
                "document_id_ac": _s("document_id_ac"),
                "document_id_pilot": _s("document_id_pilot"),
                "expense_id": _s("expense_id"),
                "snag_id": _s("snag_id"),
                "trigger_id": _s("trigger_id"),
                "wb_entry_id": _s("wb_entry_id"),
                "res_id": _s("res_id"),
                "token_id": _s("token_id"),
                "doc_id": _s("doc_id"),
                "downtime_id": _s("downtime_id"),
                "revision_id": _s("revision_id"),
                "auth_id": _s("auth_id"),
                # Tokens are ephemeral; token routes are skipped by the crawl (url=null)
                "share_token": None,
                "reset_token": None,
                "invite_token": None,
                "pilot_entry_id": _s("pilot_entry_id"),
                "pe_linked_id": _s("pe_linked_id"),
                "pe_standalone_fstd_id": _s("pe_standalone_fstd_id"),
            }
        )
        yield _E2E_BASE_URL, None, SEED
        return

    # ── In-process mode (local development) ───────────────────────────────────
    from sqlalchemy.pool import StaticPool
    from init import create_app

    # Import dev seed artefacts for credentials and TOTP secret
    from dev_seed import _DEV_TOTP_SECRET, _USERS
    from dev_seed import seed as _dev_seed

    from models import (
        Aircraft,
        AircraftPhoto,
        AirworthinessDocument,
        Component,
        Document,
        Expense,
        FlightEntry,
        LogbookEntryType,
        MaintenanceDowntime,
        MaintenanceTrigger,
        PasswordResetToken,
        PersonalMinimumsRevision,
        PilotLogbookEntry,
        RenterAuthorization,
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
        # File-based SQLite + StaticPool: a single shared connection, reused
        # across every thread the dev server touches, with SQLAlchemy's own
        # pool-checkout lock serializing access. NullPool (independent raw
        # connections per checkout) let genuinely concurrent requests — e.g.
        # several <link rel="prefetch"> firing at once — hit the same SQLite
        # file simultaneously, which surfaced as sqlite3.InterfaceError:
        # "bad parameter or other API misuse" under load (see e2e CI runs).
        # Being file-based (not :memory:), any connection still sees data
        # committed before server start, so this doesn't lose that property.
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_file}",
        SQLALCHEMY_ENGINE_OPTIONS={
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
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
        # These three are seeded against a specific aircraft/user, not c172 —
        # see the matching ac_del1/ac_stop aircraft_id override in
        # tests/e2e/test_crawl.py's _resolve_url().
        _ac_doc = (
            AirworthinessDocument.query.join(
                Component, AirworthinessDocument.component_id == Component.id
            )
            .filter(Component.aircraft_id == robin.id)
            .first()
        )
        _downtime = MaintenanceDowntime.query.filter_by(aircraft_id=seminole.id).first()
        _minimums_rev = PersonalMinimumsRevision.query.filter_by(
            user_id=admin.id
        ).first()
        _renter_auth = RenterAuthorization.query.first()

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

        # ── E2E-only extras: pilot logbook offline entries (Phase 38h-38l) ────
        # A linked entry for admin's own most-recent c172 flight (fe_flt), so
        # the aircraft workbench's "My logbook" section has something to
        # show/edit — reuse it if the dev seed already created one via the
        # unified flight form, rather than creating a second row for the
        # same flight/pilot.
        pe_linked = PilotLogbookEntry.query.filter_by(
            flight_id=fe_flt.id, pilot_user_id=admin.id
        ).first()
        if not pe_linked:
            pe_linked = PilotLogbookEntry(
                pilot_user_id=admin.id,
                flight_id=fe_flt.id,
                date=fe_flt.date,
                aircraft_type=f"{c172.make} {c172.model}",
                aircraft_registration=c172.registration,
                departure_place=fe_flt.departure_icao,
                arrival_place=fe_flt.arrival_icao,
                pic_name=admin.display_name,
                landings_day=1,
                function_pic=fe_flt.flight_time or 1,
            )
            db.session.add(pe_linked)

        # A standalone FSTD session for the standalone pilot workbench test.
        pe_standalone_fstd = PilotLogbookEntry(
            pilot_user_id=admin.id,
            date=future,
            entry_type=LogbookEntryType.FSTD,
            fstd_type="FNPT",
            fstd_duration=1.5,
            pic_name=admin.display_name,
        )
        db.session.add(pe_standalone_fstd)

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
                "ac_flt_reg": c172.registration,
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
                "pe_linked_id": pe_linked.id,
                "pe_standalone_fstd_id": pe_standalone_fstd.id,
                "doc_id": _ac_doc.id if _ac_doc else None,
                "downtime_id": _downtime.id if _downtime else None,
                "revision_id": _minimums_rev.id if _minimums_rev else None,
                "auth_id": _renter_auth.id if _renter_auth else None,
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

# Chromium does background networking of its own (Safe Browsing / component
# update / telemetry pings) independent of any page content. When the host's
# DNS is unreachable (e.g. after a router change), each of these hangs for
# the OS resolver timeout instead of failing fast, which can stall unrelated
# Playwright actions (clicks, navigations) past their default timeout and
# produce flaky, hard-to-reproduce failures. Disable it outright — mirrors
# the "fail fast instead of hang on unreachable DNS" fix in 35becae4 for the
# server-side map-tile fetches used by GIF/PNG generation.
_CHROMIUM_NO_BACKGROUND_NETWORK_ARGS = [
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disable-client-side-phishing-detection",
]


def _block_external_network(context) -> None:
    """Short-circuit external sub-resource requests (not navigations).

    The app itself also references third-party map tiles (OpenStreetMap /
    CartoDB / OpenAIP) on map-bearing pages. Those hang the same way as
    Chromium's background traffic when DNS is unreachable. No e2e test
    depends on a real external fetch succeeding (test_crawl.py explicitly
    ignores external tile/CDN failures), so short-circuiting them is safe
    and keeps the suite fast and deterministic regardless of the host's
    network state.

    Fulfilled with an empty 204 rather than aborted: route.abort() surfaces
    as a "Failed to load resource: net::ERR_FAILED" console error, which
    trips the several e2e tests that assert zero console errors across a
    full page (e.g. TestHistoryRestoreCSP, TestHtmxConsoleErrors) — those
    pages include the dashboard's Leaflet map, which requests real tiles.
    A 204 is a genuine successful response as far as the browser is
    concerned (Leaflet just renders a blank tile), so no error is logged.

    Navigation requests (top-level page loads, e.g. clicking the target=_blank
    Weblate link) are excluded and always allowed through — a 204 response to
    a navigation is a browser no-op (it never opens the new tab/page at all),
    which broke TestExternalLinksNotIntercepted. Real external navigations are
    rare in this app and not exercised in a way that hangs today.
    """

    def _handler(route):
        request = route.request
        hostname = urllib.parse.urlparse(request.url).hostname
        if hostname in ("127.0.0.1", "localhost") or request.is_navigation_request():
            route.continue_()
        else:
            route.fulfill(status=204, body="")

    context.route("**/*", _handler)


@pytest.fixture(scope="session")
def browser_context(
    live_server_url,
):  # depends on live_server_url to ensure server is up first
    from playwright.sync_api import sync_playwright

    _ignore_tls = live_server_url.startswith("https://")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=_CHROMIUM_NO_BACKGROUND_NETWORK_ARGS
        )
        context = browser.new_context(
            base_url=live_server_url,
            ignore_https_errors=_ignore_tls,
            # Block SW registration so cache.addAll() PRECACHE fetches don't
            # keep the network busy and cause networkidle timeouts.  SW behaviour
            # is tested separately in tests/test_pwa.py.
            service_workers="block",
        )
        context.set_default_timeout(10000)
        _block_external_network(context)
        yield context
        browser.close()


@pytest.fixture
def page(browser_context):
    """Fresh page per test."""
    pg = browser_context.new_page()
    yield pg
    with contextlib.suppress(Exception):
        # Cancel all in-flight requests (prefetch, XHR, fetch) before closing
        # so they don't linger in the shared browser context's connection pool
        # and starve subsequent test pages.
        pg.evaluate("() => window.stop()")
    pg.close()


@pytest.fixture
def unauthenticated_page(browser_context, live_server_url):
    """Fresh page in a brand-new browser context — no inherited session cookies.
    Used for tests that need to exercise the full login flow from scratch."""
    fresh_ctx = browser_context.browser.new_context(
        base_url=live_server_url,
        ignore_https_errors=live_server_url.startswith("https://"),
    )
    _block_external_network(fresh_ctx)
    pg = fresh_ctx.new_page()
    yield pg
    pg.close()
    fresh_ctx.close()


@pytest.fixture
def fresh_logged_in_page(browser_context, live_server_url):
    """Authenticated admin page in a fresh, isolated browser context.

    Unlike logged_in_page (which reuses the session-scoped browser context),
    this fixture creates its own context. Use it for tests that log the user
    out (e.g. clicking the logout link) so the shared session auth state is
    not destroyed and subsequent tests are unaffected.
    """
    import pyotp

    fresh_ctx = browser_context.browser.new_context(
        base_url=live_server_url,
        ignore_https_errors=live_server_url.startswith("https://"),
        service_workers="block",
    )
    fresh_ctx.set_default_timeout(10000)
    _block_external_network(fresh_ctx)
    pg = fresh_ctx.new_page()
    pg.goto(f"{live_server_url}/login")
    pg.wait_for_load_state("networkidle")
    pg.fill('input[name="email"]', "admin@openhangar.dev")
    pg.fill('input[name="password"]', "openhangar-dev-1")
    pg.click('button[type="submit"]')
    pg.wait_for_load_state("networkidle")
    if pg.locator("#totp_code").count() > 0:
        code = pyotp.TOTP(SEED["totp_secret"]).now()
        pg.locator("#totp_code").press_sequentially(code)
        try:
            pg.wait_for_url(lambda url: "/login" not in url, timeout=5000)
        except Exception:
            pg.locator('button[type="submit"]').click()
            pg.wait_for_url(lambda url: "/login" not in url, timeout=10000)
    yield pg
    pg.close()
    fresh_ctx.close()


@pytest.fixture
def fresh_viewer_page(browser_context, live_server_url):
    """Authenticated viewer (non-admin, no TOTP) page in a fresh, isolated context.

    The viewer account has no TOTP, so this fixture avoids TOTP code reuse
    conflicts when used alongside fresh_logged_in_page in the same test session.
    Use for tests that need an authenticated page for a non-destructive action
    (e.g. verifying hx-boost="false" link behaviour) and that must run in their
    own browser context without affecting the shared session.
    """
    fresh_ctx = browser_context.browser.new_context(
        base_url=live_server_url,
        ignore_https_errors=live_server_url.startswith("https://"),
        service_workers="block",
    )
    fresh_ctx.set_default_timeout(10000)
    _block_external_network(fresh_ctx)
    pg = fresh_ctx.new_page()
    pg.goto(f"{live_server_url}/login")
    pg.wait_for_load_state("networkidle")
    pg.fill('input[name="email"]', "pierre@openhangar.dev")
    pg.fill('input[name="password"]', "openhangar-dev-2")
    pg.click('button[type="submit"]')
    pg.wait_for_url(lambda url: "/login" not in url, timeout=10000)
    pg.wait_for_load_state("networkidle")
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
            # press_sequentially fires one keystroke event per digit so the
            # auto-submit JS (which counts digits on input events) triggers correctly.
            page.locator("#totp_code").press_sequentially(code)
            # Fall back to an explicit submit click on slow CI runners where the
            # JS auto-submit doesn't fire within 5 s.
            try:
                page.wait_for_url(lambda url: "/login" not in url, timeout=5000)
            except Exception:
                page.locator('button[type="submit"]').click()
                page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
    return page


# ── Setup-flow fixtures (empty-DB server) ─────────────────────────────────────


@pytest.fixture(scope="function")
def fresh_server():
    """Server with an empty database (no seed) for the setup-flow tests.

    In CI (E2E_SETUP_FLOW_BASE_URL set): delegates to the real Docker image
    running in production mode against its own disposable Postgres — no dev
    auto-seed happens in production mode (see docker-init-db.py), so it
    boots empty, giving full production parity instead of an in-process
    SQLite shortcut. The whole CI job shares one container across all setup-
    flow test functions, so each invocation truncates every table directly
    against E2E_SETUP_FLOW_DB_URL first, keeping tests isolated from
    each other despite that.

    Locally (both env vars unset): falls back to an isolated in-process
    Flask+SQLite server, one per test function, so `pytest --e2e` still
    works without Docker.
    """
    if _E2E_SETUP_FLOW_BASE_URL:
        # ── Docker / external-server mode ───────────────────────────────────
        from sqlalchemy import create_engine, text

        engine = create_engine(_E2E_SETUP_FLOW_DB_URL)
        with engine.connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename != 'alembic_version'"
                )
            ).fetchall()
            if tables:
                names = ", ".join(f'"{row[0]}"' for row in tables)
                conn.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
            conn.commit()
        engine.dispose()
        yield _E2E_SETUP_FLOW_BASE_URL
        return

    # ── In-process mode (local development, no Docker required) ────────────
    from sqlalchemy.pool import StaticPool

    from init import create_app  # type: ignore[import]
    from models import db  # type: ignore[import]

    upload_dir = tempfile.mkdtemp()
    db_file = os.path.join(upload_dir, "fresh_e2e.db")

    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
        # StaticPool: a single shared connection with SQLAlchemy's own
        # pool-checkout lock serializing access, instead of NullPool's
        # independent-connection-per-checkout (which let concurrent requests
        # hit this file simultaneously — see the matching comment above on
        # the in-process live_server fixture for the full explanation).
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_file}",
        SQLALCHEMY_ENGINE_OPTIONS={
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        },
        UPLOAD_FOLDER=upload_dir,
        SERVER_NAME=None,
    )

    with app.app_context():
        db.create_all()

    _serialize_wsgi_requests(app)

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    t = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, use_reloader=False),
        daemon=True,
    )
    t.start()
    time.sleep(0.8)

    yield f"http://127.0.0.1:{port}"

    shutil.rmtree(upload_dir, ignore_errors=True)


@pytest.fixture(scope="function")
def setup_page(fresh_server, browser_context):
    """Fresh page in an isolated context pointed at the empty-DB server.

    Re-uses the session-scoped playwright browser from browser_context so that
    the playwright event loop is initialised in the correct (non-asyncio) context.
    """
    fresh_ctx = browser_context.browser.new_context(
        base_url=fresh_server,
        ignore_https_errors=False,
        service_workers="block",
    )
    fresh_ctx.set_default_timeout(10000)
    _block_external_network(fresh_ctx)
    pg = fresh_ctx.new_page()
    yield pg, fresh_server
    pg.close()
    fresh_ctx.close()
