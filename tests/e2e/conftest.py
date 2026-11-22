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
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

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

    Flask's app.run() starts a threaded server by default, and both
    in-process servers share one SQLite connection between all threads
    (StaticPool, which does not serialize anything by itself). Under real
    browser load (several prefetches and service-worker fetches at once)
    two threads then use that connection together: first seen as
    `sqlite3.InterfaceError: bad parameter or other API misuse`, and on
    Python 3.14 with SQLite 3.54 as a segmentation fault inside SQLAlchemy's
    row processing (2026-11-17, test_htmx_boost.py / test_ui_interactions.py).
    Wrap the WSGI app in a lock so only one request is ever in flight,
    whatever the server does with connections. CI serves these suites from
    the Docker image on PostgreSQL, so it never hit this.
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


_REQUIRED_SEED_KEYS = (
    "admin_id",
    "tenant_id",
    "ac_flt",
    "ac_flt_reg",
    "ac_stop",
    "ac_del1",
    "ac_del2",
    "ac_gps",
    "ac_dup",
    "fe_flt",
    "fe_del1",
    "fe_del2",
    "dup_date",
    "dup_dep",
    "dup_arr",
    "pilot_id",
    "user_id",
    "totp_secret",
    "component_id",
    "photo_id",
    "document_id_ac",
    "document_id_pilot",
    "expense_id",
    "snag_id",
    "trigger_id",
    "wb_entry_id",
    "res_id",
    "token_id",
    "share_token",
    "reset_token",
    "invite_token",
    "pilot_entry_id",
    "pe_linked_id",
    "pe_standalone_fstd_id",
    "doc_id",
    "downtime_id",
    "revision_id",
    "auth_id",
)


def _load_seed(samples: dict) -> None:
    """Refuse incomplete or aliased fixtures instead of silently skipping tests."""
    missing = [key for key in _REQUIRED_SEED_KEYS if samples.get(key) is None]
    if missing:
        pytest.fail(
            "E2E seed is incomplete: "
            + ", ".join(missing)
            + ". Seed a disposable development instance with OPENHANGAR_E2E_SEED=1 "
            "and regenerate seed.json."
        )
    if len({samples[k] for k in ("ac_flt", "ac_stop", "ac_del1", "ac_dup")}) != 4:
        pytest.fail("E2E seed must provide four distinct fixture aircraft")
    SEED.clear()
    SEED.update(samples)


# ── Live server ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def live_server():
    """Start a live Flask server, or delegate to an external server when E2E_BASE_URL is set."""
    if _E2E_BASE_URL:
        # ── Docker / external-server mode ─────────────────────────────────────
        # The server is already running (Docker container in dev mode).
        # Seed IDs come from tests/e2e/seed.json written by generate_routes.py --seed-out.
        if not _SEED_JSON.exists():
            pytest.fail(
                "External E2E server requires seed.json from generate_routes.py --seed-out"
            )
        _load_seed(json.loads(_SEED_JSON.read_text()))
        yield _E2E_BASE_URL, None, SEED
        return

    # ── In-process mode (local development) ───────────────────────────────────
    from dev_seed import seed as _dev_seed
    from init import create_app
    from models import db
    from sqlalchemy.pool import StaticPool

    from scripts.generate_routes import _query_samples

    upload_dir = tempfile.mkdtemp()
    db_file = os.path.join(upload_dir, "e2e_test.db")
    # As CI's containers do: the global rate limit reads this when the app is
    # created, so RATELIMIT_ENABLED=False set on the config afterwards does not
    # turn it off, and a browser test clicking through pages ran into 429s.
    os.environ["OPENHANGAR_RATELIMIT_ENABLED"] = "false"
    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        # Disable Secure cookie flag so the browser sends it over http:// in E2E tests
        SESSION_COOKIE_SECURE=False,
        # File-based SQLite + StaticPool: a single shared connection, reused
        # across every thread the dev server touches; _serialize_wsgi_requests
        # (applied below) makes sure only one request uses it at a time. NullPool (independent raw
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

        # Both local and container runs use the exact same disposable extras
        # and the exact same database queries for their fixture IDs.
        with pytest.MonkeyPatch.context() as seed_env:
            seed_env.setenv("OPENHANGAR_E2E_SEED", "1")
            _dev_seed()
        _load_seed(_query_samples(app))

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
    # Count htmx settles, so a test can wait for the swap its click started
    # (click_and_settle) instead of racing it.
    context.add_init_script(
        "window.__ohSettled = 0;"
        "document.addEventListener('htmx:afterSettle', function () {"
        " window.__ohSettled += 1; });"
    )


def click_and_settle(page, locator, *, timeout: float = 15000) -> None:
    """Click an htmx-boosted control and wait until htmx has settled the
    response it swapped in: the next action then acts on the new page, not on
    one still being replaced (a click there is lost). Only for boosted
    actions — a full navigation starts a new document and never settles."""
    before = page.evaluate("() => window.__ohSettled || 0")
    locator.click()
    page.wait_for_function(
        "(n) => (window.__ohSettled || 0) > n", arg=before, timeout=timeout
    )


@pytest.fixture(scope="session")
def playwright_runtime():
    """The one Playwright instance for the whole session.

    The sync API runs its own event loop, so a second ``sync_playwright()`` in
    the same process fails ("using Playwright Sync API inside the asyncio
    loop") while this one is open. Tests that need their own browser (e.g. with
    service workers enabled) launch it from this fixture instead.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        yield playwright


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Keep each phase's report on the test item (rep_setup/rep_call/...),
    so fixtures can tell at teardown whether the test failed."""
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


TRACE_DIR = Path(__file__).resolve().parents[2] / "test-results" / "playwright-traces"


@pytest.fixture
def traced_browser(playwright_runtime, request, tmp_path):
    """A Chromium browser whose contexts record a Playwright trace.

    Every context's trace is saved when it (or the browser) closes; the files
    are kept under test-results/playwright-traces/<test>/ only if the test
    failed — open them with `playwright show-trace <file>`. CI uploads that
    folder when the browser job fails.
    """
    browser = playwright_runtime.chromium.launch(headless=True)
    open_contexts = []
    saved = []

    def stop_tracing(context):
        if context in open_contexts:
            open_contexts.remove(context)
            path = tmp_path / f"trace-{len(saved) + 1}.zip"
            try:
                context.tracing.stop(path=str(path))
                saved.append(path)
            except Exception as exc:  # noqa: BLE001 — a trace is diagnostics only
                print(f"Playwright trace not saved: {exc}")

    real_new_context = browser.new_context
    real_browser_close = browser.close

    def new_context(*args, **kwargs):
        context = real_new_context(*args, **kwargs)
        context.tracing.start(screenshots=True, snapshots=True, sources=False)
        open_contexts.append(context)
        real_close = context.close

        def close(*close_args, **close_kwargs):
            stop_tracing(context)
            return real_close(*close_args, **close_kwargs)

        context.close = close
        return context

    def close_browser(*args, **kwargs):
        while open_contexts:  # stop_tracing removes each one
            stop_tracing(open_contexts[0])
        return real_browser_close(*args, **kwargs)

    browser.new_context = new_context
    browser.close = close_browser
    yield browser
    if browser.is_connected():
        browser.close()
    failed = any(
        getattr(getattr(request.node, f"rep_{phase}", None), "failed", False)
        for phase in ("setup", "call")
    )
    if failed and saved:
        target = TRACE_DIR / request.node.name
        target.mkdir(parents=True, exist_ok=True)
        for path in saved:
            shutil.move(path, target / path.name)  # tmp may be another filesystem
        print(f"Playwright traces kept in {target}")


@pytest.fixture(scope="session")
def browser_context(
    live_server_url, playwright_runtime
):  # depends on live_server_url to ensure server is up first
    _ignore_tls = live_server_url.startswith("https://")
    browser = playwright_runtime.chromium.launch(
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


def _admin_login(page) -> None:
    """Sign in as the dev-seed admin from the login page *page* is on.

    The admin has TOTP (JBSWY3DPEHPK3PXP), and the app refuses a code from a
    30-second step already used (replay protection): two browser workers,
    or a logout test, signing in within the same step would see the second
    refused. A refused code — or one about to expire — waits for the next
    step and types that step's fresh code. The auto-submit JS submits on the
    6th digit (press_sequentially fires one input event per digit); an
    explicit click covers a slow runner where it didn't.
    """
    import pyotp

    page.fill('input[name="email"]', "admin@openhangar.dev")
    page.fill('input[name="password"]', "openhangar-dev-1")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    totp = pyotp.TOTP(SEED["totp_secret"])
    for _attempt in range(3):
        if page.locator("#totp_code").count() == 0:
            return
        remaining = 30 - time.time() % 30
        if remaining < 3:
            time.sleep(remaining + 0.2)
        step = int(time.time() // 30)
        page.locator("#totp_code").fill("")
        page.locator("#totp_code").press_sequentially(totp.now())
        try:
            page.wait_for_url(lambda url: "/login" not in url, timeout=5000)
            return
        except PlaywrightTimeoutError:
            if "/login" not in page.url:
                return
            # Still holding the typed digits: the auto-submit never fired.
            # A refused code comes back as the TOTP step with an empty field.
            field = page.locator("#totp_code")
            if field.count() and len(field.input_value()) == 6:
                page.locator('button[type="submit"]').click()
                page.wait_for_load_state("networkidle")
                if "/login" not in page.url:
                    return
        # Refused (another login took this step): wait for the next one.
        time.sleep(max(0.0, (step + 1) * 30 - time.time()) + 0.2)
    raise AssertionError("the admin could not sign in: every TOTP code was refused")


@pytest.fixture
def fresh_logged_in_page(browser_context, live_server_url):
    """Authenticated admin page in a fresh, isolated browser context.

    Unlike logged_in_page (which reuses the session-scoped browser context),
    this fixture creates its own context. Use it for tests that log the user
    out (e.g. clicking the logout link) so the shared session auth state is
    not destroyed and subsequent tests are unaffected.
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
    _admin_login(pg)
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

    page.goto(f"{live_server_url}/login")
    page.wait_for_load_state("networkidle")
    if "/login" in page.url:
        _admin_login(page)
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
        from init import _normalize_database_url  # type: ignore[import]
        from sqlalchemy import create_engine, text

        engine = create_engine(_normalize_database_url(_E2E_SETUP_FLOW_DB_URL))
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
    from init import create_app  # type: ignore[import]
    from models import db  # type: ignore[import]
    from sqlalchemy.pool import StaticPool

    upload_dir = tempfile.mkdtemp()
    db_file = os.path.join(upload_dir, "fresh_e2e.db")

    # As CI's containers do: the global rate limit reads this when the app is
    # created, so RATELIMIT_ENABLED=False set on the config afterwards does not
    # turn it off, and a browser test clicking through pages ran into 429s.
    os.environ["OPENHANGAR_RATELIMIT_ENABLED"] = "false"
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
