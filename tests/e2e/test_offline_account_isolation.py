"""Real service-worker/IndexedDB boundaries across accounts in one browser."""

import threading
import time
from datetime import date

import pytest
from playwright.sync_api import Page

pytestmark = pytest.mark.e2e

PASSWORD = "OfflineBrowserPassword123!"


@pytest.fixture
def isolated_offline_server(tmp_path, monkeypatch):
    import pw_hash
    from init import create_app
    from models import (
        Flight,
        OperatingModel,
        Role,
        Tenant,
        TenantProfile,
        TenantUser,
        User,
        db,
    )
    from werkzeug.serving import make_server

    monkeypatch.setenv(
        "OPENHANGAR_DATABASE_URL", f"sqlite:///{tmp_path / 'offline.db'}"
    )
    monkeypatch.setenv("OPENHANGAR_SKIP_BACKGROUND_THREADS", "1")
    monkeypatch.setenv("OPENHANGAR_ENV", "test")
    monkeypatch.setenv("OPENHANGAR_SW_ENABLED", "true")
    monkeypatch.setenv("OPENHANGAR_VERSION", "offline-account-browser-test")
    app = create_app()
    app.config.update(
        TESTING=True, UPLOAD_FOLDER=str(tmp_path), RATELIMIT_ENABLED=False
    )
    users = {}
    with app.app_context():
        db.create_all()
        for label in ("Alpha", "Bravo"):
            tenant = Tenant(name=f"Private {label} organisation")
            user = User(
                email=f"{label.lower()}@offline.example",
                name=f"Private {label} pilot",
                password_hash=pw_hash.hash(PASSWORD),
            )
            db.session.add_all([tenant, user])
            db.session.flush()
            db.session.add(
                TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
            )
            db.session.add(
                TenantProfile(
                    tenant_id=tenant.id,
                    operating_model=OperatingModel.SOLE_PILOT,
                    planned_aircraft_count=0,
                    setup_complete=True,
                )
            )
            db.session.add(
                Flight(
                    pic_user_id=user.id,
                    date=date(2026, 1, 2),
                    other_aircraft_registration=f"{label.upper()}-PRIVATE",
                    departure_icao="EBNM",
                    arrival_icao="EBNM",
                    flight_time=1,
                )
            )
            users[label] = user.id
        db.session.commit()
    requests = []
    slow_entered = threading.Event()
    slow_release = threading.Event()

    @app.before_request
    def record_requests():
        from flask import request, session

        requests.append((request.method, request.path, session.get("user_id")))
        if request.path == "/pilot/logbook" and request.args.get("hold"):
            slow_entered.set()
            slow_release.wait(timeout=15)

    @app.route("/test-only/save", methods=["POST"])
    def test_only_save():
        """A save as any form makes one: change data, redirect to a page."""
        from flask import flash, redirect, request, session

        flight = Flight.query.filter_by(pic_user_id=session["user_id"]).one()
        flight.other_aircraft_registration = request.form["registration"]
        db.session.commit()
        flash("TEST-SAVED-ONCE", "success")
        return redirect("/pilot/minimums")

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            app,
            f"http://127.0.0.1:{server.server_port}",
            users,
            requests,
            slow_entered,
            slow_release,
        )
    finally:
        slow_release.set()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def wait_until(page, expression, arg=None, timeout=10000):
    """Poll an async page expression until it is truthy.

    Playwright's wait_for_function does not await a returned Promise: a
    Promise object is truthy, so it returns at once. page.evaluate does await.
    """
    deadline = time.monotonic() + timeout / 1000
    while not page.evaluate(expression, arg):
        if time.monotonic() > deadline:
            raise TimeoutError(f"Timed out waiting for: {expression}")
        page.wait_for_timeout(100)


def wait_for_offline_pilot_logbook(page, marker):
    """Wait until this login's pilot snapshot holds `marker` and the offline
    pilot logbook page is in its private page cache."""
    wait_until(
        page,
        "marker => OhOffline.getPilotSnapshot().then(s => !!s && JSON.stringify(s).includes(marker))",
        marker,
    )
    wait_until(
        page,
        """() => caches.keys().then(names => Promise.all(names.filter(n => n.includes('-private-'))
             .map(n => caches.open(n).then(c => c.match(location.origin + '/pilot/logbook/offline')))))
             .then(found => found.some(Boolean))""",
    )
    wait_for_settled_worker(page)


def login(page, origin, label):
    page.on("pageerror", lambda error: print("BROWSER ERROR", str(error)))
    page.on(
        "console",
        lambda message: (
            print("BROWSER CONSOLE", message.text) if message.type == "error" else None
        ),
    )
    page.goto(origin + "/login", wait_until="load")
    page.locator("#email").fill(f"{label.lower()}@offline.example")
    page.locator("#password").fill(PASSWORD)
    page.locator('button[type="submit"]').click()
    page.wait_for_load_state("load")
    try:
        page.wait_for_function(
            "() => !!window.OhOfflineIdentity?.current()?.owner", timeout=10000
        )
        wait_until(
            page,
            "() => navigator.serviceWorker.getRegistration().then(reg => !!reg && !!reg.active)",
            timeout=10000,
        )
        page.goto(origin + "/pilot/logbook", wait_until="load")
        page.wait_for_function(
            "() => !!navigator.serviceWorker.controller", timeout=10000
        )
        page.wait_for_function(
            "() => !!window.OhOfflineIdentity.current()", timeout=10000
        )
    except Exception:
        print(
            "OFFLINE STATE",
            label,
            page.url,
            page.evaluate(
                """() => ({body:document.body.textContent.slice(0,300), owner:document.body.dataset.ohOfflineOwner, current:window.OhOfflineIdentity?.current(), saved:localStorage.getItem('oh-offline-active-identity'), controlled:!!navigator.serviceWorker.controller, config:window._oh_config})"""
            ),
        )
        raise
    return page.evaluate("() => window.OhOfflineIdentity.current()")


def wait_for_settled_worker(page):
    """Wait until no service-worker update is installing or waiting, so a test
    does not go offline while a new worker is still taking over."""
    wait_until(
        page,
        """() => navigator.serviceWorker.getRegistration().then(reg =>
            !!reg && !!reg.active && reg.active.state === 'activated'
            && !reg.installing && !reg.waiting)""",
    )


def test_account_switch_never_shows_the_previous_accounts_logbook(
    isolated_offline_server,
    traced_browser,
):
    _app, origin, _users, _, _, _ = isolated_offline_server
    browser = traced_browser
    try:
        context = browser.new_context(service_workers="allow")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        alpha = login(page, origin, "Alpha")
        assert "ALPHA-PRIVATE" in page.content()
        wait_for_offline_pilot_logbook(page, "ALPHA-PRIVATE")
        other_tab = context.new_page()
        other_tab.goto(origin + "/pilot/logbook", wait_until="load")
        page.goto(origin + "/logout", wait_until="load")
        bravo = login(page, origin, "Bravo")
        assert bravo["owner"] != alpha["owner"]
        other_tab.wait_for_function(
            "() => !document.body.textContent.includes('ALPHA-PRIVATE')"
        )
        # Bravo may already have refreshed their own snapshot in the background;
        # what must never be visible here is Alpha's.
        snapshot = page.evaluate("() => OhOffline.getPilotSnapshot()")
        assert snapshot is None or "ALPHA" not in repr(snapshot)
        wait_for_offline_pilot_logbook(page, "BRAVO-PRIVATE")
        context.set_offline(True)
        for path in ("/pilot/logbook", "/pilot/logbook/offline"):
            page.goto(origin + path, wait_until="load")
            assert "ALPHA-PRIVATE" not in page.content()
        page.wait_for_function(
            "() => document.body.textContent.includes('BRAVO-PRIVATE')"
        )
        assert not errors
        context.close()
    finally:
        browser.close()


def test_offline_pilot_logbook_reads_the_whole_logbook_without_a_connection(
    isolated_offline_server,
    traced_browser,
):
    _app, origin, _users, requests, _, _ = isolated_offline_server
    browser = traced_browser
    try:
        context = browser.new_context(service_workers="allow")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        login(page, origin, "Alpha")  # ends on /pilot/logbook: snapshot + precache
        wait_for_offline_pilot_logbook(page, "ALPHA-PRIVATE")
        context.set_offline(True)
        page.goto(origin + "/pilot/logbook/offline", wait_until="load")
        page.wait_for_function(
            "() => document.querySelector('[data-oh-offline-rows]').textContent.includes('ALPHA-PRIVATE')"
        )
        assert page.locator("[data-oh-offline-banner]").is_visible()
        assert page.locator("#oh-offline-logbook input").count() == 0
        writes = [item for item in requests if item[0] == "POST"]
        context.set_offline(False)
        assert [item for item in requests if item[0] == "POST"] == writes
        assert not errors
        context.close()
    finally:
        browser.close()


def test_upgrade_removes_offline_editing_data(
    isolated_offline_server,
    traced_browser,
):
    _app, origin, _users, _, _, _ = isolated_offline_server
    browser = traced_browser
    try:
        context = browser.new_context(service_workers="allow")
        page = context.new_page()
        alpha = login(page, origin, "Alpha")
        # Set up from a page that holds none of these databases open (the
        # app's pages do: a deletion would be blocked and race the setup).
        page.goto(origin + "/static/pwa/offline.html", wait_until="load")
        # What the previous release left behind: the legacy unowned database,
        # and version 3 account databases holding pending writes.
        page.evaluate(
            """owner => Promise.all(['openhangar-offline', 'openhangar-offline-' + owner,
                 'openhangar-offline-another-account'].map(name => new Promise((resolve, reject) => {
              const old = indexedDB.deleteDatabase(name);
              old.onsuccess = old.onerror = old.onblocked = () => {
                const req = indexedDB.open(name, 3);
                req.onupgradeneeded = () => ['queue', 'outbox', 'pilot_outbox']
                  .forEach(store => req.result.createObjectStore(store, { keyPath: 'id', autoIncrement: true }));
                req.onsuccess = () => {
                  const tx = req.result.transaction('outbox', 'readwrite');
                  tx.objectStore('outbox').add({ fields: { notes: 'PENDING' } });
                  tx.oncomplete = () => { req.result.close(); resolve(); };
                };
                req.onerror = () => reject(req.error);
              };
            })))""",
            alpha["owner"],
        )
        page.goto(origin + "/pilot/logbook", wait_until="load")
        wait_until(
            page,
            """() => indexedDB.databases().then(dbs =>
                 !dbs.some(db => db.name === 'openhangar-offline')
                 && dbs.filter(db => db.name.startsWith('openhangar-offline-')).every(db => db.version === 4))""",
        )
        stores = page.evaluate(
            """() => new Promise(resolve => {
              const req = indexedDB.open('openhangar-offline-another-account');
              req.onsuccess = () => { const names = [...req.result.objectStoreNames]; req.result.close(); resolve(names); };
            })"""
        )
        assert stores == ["pilot_snapshot", "snapshots"]
        context.close()
    finally:
        browser.close()


def test_logout_permission_loss_and_worker_upgrade_retire_private_cache(
    isolated_offline_server,
    traced_browser,
    monkeypatch,
):
    from models import User, db

    app, origin, users, _, _, _ = isolated_offline_server
    browser = traced_browser
    try:
        context = browser.new_context(service_workers="allow")
        page = context.new_page()
        alpha = login(page, origin, "Alpha")
        page.evaluate(
            """() => caches.open('old-unscoped-cache').then(c =>
                 c.put('/pilot/logbook', new Response('ALPHA-OLD-CACHE')))"""
        )
        # A real upgrade: same /sw.js URL, new content (the cache version comes
        # from OPENHANGAR_VERSION, read on every request), picked up by update().
        monkeypatch.setenv("OPENHANGAR_VERSION", "offline-account-browser-test-v2")
        page.evaluate(
            "() => navigator.serviceWorker.getRegistration().then(reg => reg.update())"
        )
        wait_until(
            page,
            """() => caches.keys().then(names => !names.includes('old-unscoped-cache')
                 && names.includes('openhangar-offline-account-browser-test-v2'))""",
        )
        wait_for_settled_worker(page)
        with app.app_context():
            db.session.get(User, users["Alpha"]).is_active = False
            db.session.commit()
        page.evaluate("() => fetch('/api/offline/identity').then(r => r.status)")
        page.wait_for_function(
            "() => !document.body.textContent.includes('ALPHA-PRIVATE')"
        )
        wait_until(
            page,
            "owner => caches.keys().then(names => !names.some(name => name.includes('-private-' + owner)))",
            arg=alpha["owner"],
        )
        wait_for_settled_worker(page)
        context.set_offline(True)
        page.goto(origin + "/pilot/logbook", wait_until="load")
        assert "ALPHA-PRIVATE" not in page.content()
        context.close()
    finally:
        browser.close()


def test_old_inflight_account_response_cannot_repopulate_a_retired_cache(
    isolated_offline_server,
    traced_browser,
):

    _, origin, _, _, entered, release = isolated_offline_server
    browser = traced_browser
    try:
        context = browser.new_context(service_workers="allow")
        first = context.new_page()
        alpha = login(first, origin, "Alpha")
        first.evaluate(
            "() => { window.heldResponse = fetch('/pilot/logbook?hold=1').then(r => r.text()).catch(() => 'retired'); }"
        )
        assert entered.wait(timeout=10)
        # The request stays held (server side) across the account switch and
        # is released only once Bravo has signed in: the worker must not cache
        # what a response started under Alpha's login brings back. (No worker
        # swap here: the browser would not complete one while this request is
        # still being answered, so the hold would time out first and the
        # response arrive before the switch, testing nothing.)
        second = context.new_page()
        second.goto(origin + "/logout", wait_until="load")
        login(second, origin, "Bravo")
        assert not release.is_set() and entered.is_set()  # still held
        release.set()
        first.evaluate("() => window.heldResponse")
        wait_until(
            second,
            "owner => caches.keys().then(names => !names.some(name => name.includes('-private-' + owner)))",
            arg=alpha["owner"],
        )
        context.set_offline(True)
        second.goto(origin + "/pilot/logbook", wait_until="load")
        assert "BRAVO-PRIVATE" in second.content()
        assert "ALPHA-PRIVATE" not in second.content()
        context.close()
    finally:
        release.set()
        browser.close()


def test_aircraft_grant_loss_retires_cached_page_and_snapshot(
    isolated_offline_server, traced_browser
):
    from models import Aircraft, Flight, Role, TenantUser, UserAircraftAccess, db

    app, origin, users, _, _, _ = isolated_offline_server
    with app.app_context():
        membership = TenantUser.query.filter_by(user_id=users["Alpha"]).one()
        membership.role = Role.PILOT
        aircraft = Aircraft(
            tenant_id=membership.tenant_id,
            registration="GRANT-PRIVATE",
            make="Test",
            model="172",
        )
        db.session.add(aircraft)
        db.session.flush()
        aircraft_id = aircraft.id
        db.session.add(
            UserAircraftAccess(user_id=users["Alpha"], aircraft_id=aircraft_id)
        )
        db.session.add(
            Flight(
                aircraft_id=aircraft_id,
                date=date(2026, 1, 3),
                notes="AIRCRAFT-GRANT-PRIVATE",
                flight_time=1,
            )
        )
        db.session.commit()
    browser = traced_browser
    try:
        context = browser.new_context(service_workers="allow")
        page = context.new_page()
        login(page, origin, "Alpha")
        target = f"/aircraft/{aircraft_id}/flights"
        page.goto(origin + target, wait_until="load")
        wait_until(
            page,
            "id => OhOffline.getSnapshot(id).then(value => !!value)",
            arg=aircraft_id,
        )
        assert "AIRCRAFT-GRANT-PRIVATE" in page.content()
        with app.app_context():
            UserAircraftAccess.query.filter_by(
                user_id=users["Alpha"], aircraft_id=aircraft_id
            ).delete()
            db.session.commit()
        # Instant screens: the first visit after losing access may still show
        # the saved copy at once (agreed: one more view of a page already
        # seen); the server's 404 then removes it in the background. The
        # page's own snapshot refresh may get its 404 first and retire this
        # whole login (the stronger response). Either way, from then on the
        # private page is never served again.
        page.evaluate("url => fetch(url).then(r => r.text())", target)
        wait_until(
            page,
            """url => caches.keys().then(names => Promise.all(names
                 .filter(n => n.includes('-private-'))
                 .map(n => caches.open(n).then(c => c.match(location.origin + url)))))
                 .then(found => !found.some(Boolean))""",
            arg=target,
        )
        result = page.evaluate(
            "url => fetch(url).then(async r => ({status: r.status, text: await r.text()}))",
            target,
        )
        assert "AIRCRAFT-GRANT-PRIVATE" not in result["text"]
        assert result["status"] == 404 or not page.evaluate(
            "() => !!window.OhOfflineIdentity.current()"
        )
        # The corresponding snapshot denial also quarantines the old epoch.
        page.evaluate(
            "async id => { try { await OhOfflineIdentity.fetch('/api/offline/aircraft/'+id+'/logbook'); } catch (_) {} }",
            aircraft_id,
        )
        page.wait_for_function(
            "() => !document.body.textContent.includes('AIRCRAFT-GRANT-PRIVATE')"
        )
        context.set_offline(True)
        page.goto(origin + target, wait_until="load")
        assert "AIRCRAFT-GRANT-PRIVATE" not in page.content()
        context.close()
    finally:
        browser.close()


def test_own_password_change_renews_context_without_a_login_dead_end(
    isolated_offline_server,
    traced_browser,
):

    _app, origin, _users, _, _, _ = isolated_offline_server
    browser = traced_browser
    try:
        context = browser.new_context(service_workers="allow")
        page = context.new_page()
        before = login(page, origin, "Alpha")
        page.goto(origin + "/profile", wait_until="load")
        form = page.locator('form:has(input[value="change_password"])')
        form.locator('[name="current_password"]').fill(PASSWORD)
        form.locator('[name="new_password"]').fill("RenewedBrowserPassword123!")
        form.locator('[name="confirm_password"]').fill("RenewedBrowserPassword123!")
        form.locator('button[type="submit"]').click()
        page.wait_for_function(
            # Guarded: the condition is also evaluated while the next page loads.
            "previous => !!window.OhOfflineIdentity?.current() && OhOfflineIdentity.current().session !== previous",
            arg=before["session"],
        )
        after = page.evaluate("() => OhOfflineIdentity.current()")
        assert after["owner"] == before["owner"]
        assert form.locator('[name="current_password"]').is_visible()
        context.close()
    finally:
        browser.close()


def test_login_expired_while_offline_is_retired_on_reconnect(
    isolated_offline_server,
    traced_browser,
):
    """Offline, the device cannot know the login expired. Once it is back
    online, the first private page proves it, and the account's cached
    pages and snapshot stop being served — also offline afterwards."""
    from models import AuthSession, db

    app, origin, users, _, _, _ = isolated_offline_server
    context = traced_browser.new_context(service_workers="allow")
    page = context.new_page()
    login(page, origin, "Alpha")
    wait_for_offline_pilot_logbook(page, "ALPHA-PRIVATE")
    context.set_offline(True)
    page.goto(origin + "/pilot/logbook", wait_until="load")
    assert "ALPHA-PRIVATE" in page.content()  # offline viewing still works

    with app.app_context():
        AuthSession.query.filter_by(user_id=users["Alpha"]).update(
            {"expires_at": 1}, synchronize_session=False
        )
        db.session.commit()
    context.set_offline(False)
    # The device still believes the login valid (its end date is weeks away),
    # so the saved copy shows at once; the server's answer in the background
    # proves the login ended: saved pages are deleted and the tab locks.
    page.goto(origin + "/pilot/logbook", wait_until="load")
    wait_until(
        page,
        "() => caches.keys().then(names => !names.some(n => n.includes('-private-')))",
    )
    page.wait_for_function("() => !document.body.textContent.includes('ALPHA-PRIVATE')")
    page.goto(origin + "/pilot/logbook", wait_until="load")
    assert "/login" in page.url
    assert page.evaluate("() => OhOffline.getPilotSnapshot()") is None

    wait_for_settled_worker(page)
    context.set_offline(True)
    for path in ("/pilot/logbook", "/pilot/logbook/offline"):
        page.goto(origin + path, wait_until="load")
        assert "ALPHA-PRIVATE" not in page.content()
    context.close()


def _delay(app, path, seconds):
    """Make the live test server answer `path` slowly (or normally: 0)."""
    from flask import request

    def slow():
        if request.path == path:
            time.sleep(seconds)

    funcs = app.before_request_funcs.setdefault(None, [])
    funcs[:] = [f for f in funcs if getattr(f, "_oh_delay", False) is False]
    if seconds:
        slow._oh_delay = True
        funcs.insert(0, slow)


def _wait_for_cached_text(page: Page, path: str, marker: str) -> None:
    """Wait for the worker's actual cached response, including its body."""
    wait_until(
        page,
        """({path, marker}) => caches.keys().then(names => Promise.all(
            names.filter(n => n.includes('-private-')).map(n => caches.open(n)
                .then(c => c.match(location.origin + path))
                .then(r => r ? r.text() : ''))))
            .then(texts => texts.some(text => text.includes(marker)))""",
        {"path": path, "marker": marker},
        timeout=15000,
    )


def test_saved_page_shows_instantly_while_the_server_is_busy(
    isolated_offline_server,
    traced_browser,
):
    """A cold page load (address bar, app start) shows this login's saved
    copy at once, not after a timeout, then refreshes it from the server."""
    from models import Flight, db

    app, origin, users, _, slow_entered, slow_release = isolated_offline_server
    context = traced_browser.new_context(service_workers="allow")
    page = context.new_page()
    login(page, origin, "Alpha")
    wait_for_settled_worker(page)

    # Warm a real, distinct navigation response. Background precaching of the
    # ordinary logbook URL must not replace this test's baseline after the DB
    # changes. The fixture's gate lets the warm request finish immediately.
    probe = "/pilot/logbook?hold=1"
    slow_release.set()
    page.goto(origin + probe, wait_until="load")
    _wait_for_cached_text(page, probe, "ALPHA-PRIVATE")
    slow_entered.clear()
    slow_release.clear()
    try:
        with app.app_context():
            flight = Flight.query.filter_by(pic_user_id=users["Alpha"]).one()
            flight.other_aircraft_registration = "ALPHA-FRESH"
            db.session.commit()

        page.goto(origin + probe, wait_until="commit")
        page.wait_for_function(
            "() => document.body && document.body.textContent.includes('ALPHA-PRIVATE')",
            timeout=2000,
        )
        # The cached copy is visible while the actual request is still held
        # (it is sent in the background, so it may reach the server just
        # after); no wall-clock race against an arbitrary server sleep.
        assert slow_entered.wait(timeout=10)
        assert not slow_release.is_set()
        slow_release.set()
        _wait_for_cached_text(page, probe, "ALPHA-FRESH")
    finally:
        slow_release.set()

    # Another account: Alpha's copy is gone with the switch, so a slow
    # network makes Bravo wait for the real page, never see Alpha's.
    page.goto(origin + "/logout", wait_until="load")
    login(page, origin, "Bravo")
    wait_for_settled_worker(page)
    _delay(app, "/pilot/logbook", 4)
    try:
        # Use the identical cache key: a different account must never inherit it.
        page.goto(origin + probe, wait_until="load")
        assert "ALPHA" not in page.content()
        assert "BRAVO-PRIVATE" in page.content()
    finally:
        _delay(app, "/pilot/logbook", 0)
    context.close()


def _boosted_visit(page, path):
    """Leave the current page through an htmx-boosted link, as a click in the
    app does: htmx saves the page being left into its back-button cache."""
    page.evaluate(
        """path => { const a = document.createElement('a'); a.href = path;
             a.id = 'oh-test-boosted-link'; a.textContent = path;
             document.body.appendChild(a); htmx.process(a); }""",
        path,
    )
    page.locator("#oh-test-boosted-link").click()
    page.wait_for_url("**" + path)
    page.wait_for_load_state("load")


def test_back_button_cache_does_not_outlive_the_account(
    isolated_offline_server, traced_browser
):
    """htmx's back-button copy of visited pages (sessionStorage) is dropped on
    a signed-out page, and another account starts the tab without it."""
    _, origin, _, _, _, _ = isolated_offline_server
    browser = traced_browser
    history = "() => sessionStorage.getItem('htmx-history-cache') || ''"
    try:
        context = browser.new_context(service_workers="allow")
        page = context.new_page()
        login(page, origin, "Alpha")  # ends on /pilot/logbook
        _boosted_visit(page, "/pilot/minimums")
        assert "ALPHA-PRIVATE" in page.evaluate(history)  # the cache is in use

        page.goto(origin + "/logout", wait_until="load")
        assert page.evaluate(history) == ""
        # A stale navigation hint must not mint a competing login CSRF cookie.
        speculative = context.request.get(
            origin + "/login", headers={"Sec-Purpose": "prefetch"}
        )
        assert speculative.status == 204
        # "no-store", or "no-store, private" when the request still saw the
        # session it just ended (timing): both forbid any cache.
        assert "no-store" in speculative.headers["cache-control"]
        assert "set-cookie" not in speculative.headers

        login(page, origin, "Bravo")
        _boosted_visit(page, "/pilot/minimums")
        cached = page.evaluate(history)
        assert "BRAVO-PRIVATE" in cached and "ALPHA-PRIVATE" not in cached
        context.close()
    finally:
        browser.close()


def _gate(app, path):
    """Hold the live server's answers for `path` until `release` is set.
    `_delay(app, path, 0)` removes every gate and delay."""
    from flask import request

    entered, release = threading.Event(), threading.Event()

    def hold():
        if request.path == path:
            entered.set()
            release.wait(timeout=15)

    hold._oh_delay = True
    app.before_request_funcs.setdefault(None, []).insert(0, hold)
    return entered, release


def _quiet(page, requests, seconds=1.5):
    """Wait until the browser has sent the server nothing for `seconds`:
    background checks and prefetches started by the last page are done."""
    deadline = time.monotonic() + 15
    seen = len(requests)
    calm_since = time.monotonic()
    while time.monotonic() - calm_since < seconds:
        if time.monotonic() > deadline:
            raise TimeoutError("the browser kept sending requests")
        page.wait_for_timeout(200)
        if len(requests) != seen:
            seen = len(requests)
            calm_since = time.monotonic()


def _boosted_click(page, path):
    """Click an htmx-boosted link to `path` without waiting for the page."""
    page.evaluate(
        """path => { const a = document.createElement('a'); a.href = path;
             a.id = 'oh-test-boosted-click'; a.textContent = path;
             document.body.appendChild(a); htmx.process(a); }""",
        path,
    )
    page.locator("#oh-test-boosted-click").click()


def _cached_text(page, path):
    return page.evaluate(
        """path => caches.keys().then(names => Promise.all(names
             .filter(n => n.includes('-private-'))
             .map(n => caches.open(n).then(c => c.match(location.origin + path))
               .then(r => r ? r.text() : ''))))
             .then(texts => texts.join(''))""",
        path,
    )


def _still_waiting_for_server(page, entered, path_before):
    """The server has the request, and the page has not changed meanwhile."""
    assert entered.wait(timeout=10)
    page.wait_for_timeout(500)
    assert page.evaluate("() => location.pathname") == path_before


def test_a_save_refreshes_main_pages_and_outdates_the_others(
    isolated_offline_server, traced_browser
):
    """After a save, the main pages are refreshed in the background and open
    instantly with the change; another saved page comes from the server once,
    then is instant again."""
    app, origin, _users, _, _, _ = isolated_offline_server
    releases = []
    context = traced_browser.new_context(service_workers="allow")
    page = context.new_page()
    try:
        login(page, origin, "Alpha")  # ends on /pilot/logbook
        wait_for_settled_worker(page)
        _boosted_visit(page, "/pilot/stats")
        _wait_for_cached_text(page, "/pilot/logbook", "ALPHA-PRIVATE")
        wait_until(page, "() => true")  # let the worker store /pilot/stats
        assert _cached_text(page, "/pilot/stats")

        page.evaluate(
            """() => { const f = document.createElement('form');
                 f.method = 'post'; f.action = '/test-only/save'; f.id = 'oh-test-save';
                 f.innerHTML = '<input name="registration" value="ALPHA-SAVED">'
                   + '<input type="hidden" name="csrf_token"><button type="submit">Save</button>';
                 f.elements.csrf_token.value =
                   document.querySelector('meta[name="csrf-token"]').content;
                 document.body.appendChild(f); htmx.process(f); }"""
        )
        page.locator("#oh-test-save button").click()
        page.wait_for_url("**/pilot/minimums")
        # The landing page shows the one-time message: never saved with it.
        assert "TEST-SAVED-ONCE" in page.content()
        # A main page is refreshed without being opened...
        _wait_for_cached_text(page, "/pilot/logbook", "ALPHA-SAVED")
        assert "TEST-SAVED-ONCE" not in _cached_text(page, "/pilot/minimums")
        # ...so it opens instantly, already showing the change.
        entered, release = _gate(app, "/pilot/logbook")
        releases.append(release)
        _boosted_visit(page, "/pilot/logbook")
        assert "ALPHA-SAVED" in page.content()
        release.set()
        _delay(app, "/pilot/logbook", 0)

        # Another saved page is outdated by the save: from the server once...
        entered, release = _gate(app, "/pilot/stats")
        releases.append(release)
        _boosted_click(page, "/pilot/stats")
        _still_waiting_for_server(page, entered, "/pilot/logbook")
        release.set()
        page.wait_for_url("**/pilot/stats")
        _delay(app, "/pilot/stats", 0)
        # ...then instant again.
        _boosted_visit(page, "/pilot/logbook")
        _entered, release = _gate(app, "/pilot/stats")
        releases.append(release)
        _boosted_visit(page, "/pilot/stats")
    finally:
        for release in releases:
            release.set()
        _delay(app, "/pilot/stats", 0)
        context.close()


def test_instant_screens_off_asks_the_server_first(
    isolated_offline_server, traced_browser
):
    """OPENHANGAR_INSTANT_PAGES=false: each device follows at the next server
    answer, and a saved page no longer shows before the server's."""
    app, origin, _users, _, _, _ = isolated_offline_server
    context = traced_browser.new_context(service_workers="allow")
    page = context.new_page()
    release = threading.Event()
    try:
        login(page, origin, "Alpha")  # ends on /pilot/logbook
        wait_for_settled_worker(page)
        _wait_for_cached_text(page, "/pilot/logbook", "ALPHA-PRIVATE")
        _boosted_visit(page, "/pilot/minimums")
        app.config["INSTANT_PAGES"] = False
        page.evaluate("() => fetch('/api/offline/identity').then(r => r.status)")
        entered, release = _gate(app, "/pilot/logbook")
        _boosted_click(page, "/pilot/logbook")
        _still_waiting_for_server(page, entered, "/pilot/minimums")
        release.set()
        page.wait_for_url("**/pilot/logbook")
    finally:
        app.config["INSTANT_PAGES"] = True
        release.set()
        _delay(app, "/pilot/logbook", 0)
        context.close()


def test_login_end_date_deletes_saved_pages_without_a_connection(
    isolated_offline_server, traced_browser
):
    """The device knows when its login ends: past that, saved pages are
    deleted even with no connection to ask the server."""
    from models import AuthSession, db

    app, origin, users, requests, _, _ = isolated_offline_server
    context = traced_browser.new_context(service_workers="allow")
    page = context.new_page()
    login(page, origin, "Alpha")
    wait_for_settled_worker(page)
    _wait_for_cached_text(page, "/pilot/logbook", "ALPHA-PRIVATE")
    # An answer to a request started earlier would still bring the old end date.
    _quiet(page, requests)
    with app.app_context():
        AuthSession.query.filter_by(user_id=users["Alpha"]).update(
            {"expires_at": int(time.time()) + 3}, synchronize_session=False
        )
        db.session.commit()
    # One answer tells the worker the new end date; then no more server.
    page.evaluate("() => fetch('/api/offline/identity').then(r => r.status)")
    context.set_offline(True)
    page.wait_for_timeout(4500)
    page.goto(origin + "/pilot/logbook", wait_until="load")
    assert "ALPHA-PRIVATE" not in page.content()
    wait_until(
        page,
        "() => caches.keys().then(names => !names.some(n => n.includes('-private-')))",
    )
    context.close()


def test_coming_back_to_the_app_refreshes_main_pages_saved_long_ago(
    isolated_offline_server, traced_browser
):
    _app, origin, _users, requests, _, _ = isolated_offline_server
    context = traced_browser.new_context(service_workers="allow")
    page = context.new_page()
    login(page, origin, "Alpha")  # ends on /pilot/logbook
    wait_for_settled_worker(page)
    _wait_for_cached_text(page, "/pilot/logbook", "ALPHA-PRIVATE")
    _boosted_visit(page, "/pilot/minimums")
    # Checks and prefetches started by these pages would overwrite the copy.
    _quiet(page, requests)
    # Pretend the logbook copy was saved 20 minutes ago.
    page.evaluate(
        """async () => {
          for (const name of (await caches.keys()).filter(n => n.includes('-private-'))) {
            const cache = await caches.open(name);
            const key = location.origin + '/pilot/logbook';
            const old = await cache.match(key);
            if (!old) continue;
            const headers = new Headers(old.headers);
            headers.set('X-OH-Saved-At', String(Date.now() - 20 * 60 * 1000));
            await cache.put(key, new Response('STALE-COPY', { status: 200, headers }));
          }
        }"""
    )
    assert "STALE-COPY" in _cached_text(page, "/pilot/logbook")
    # The app comes back to the foreground (no navigation, so no prefetch).
    page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    _wait_for_cached_text(page, "/pilot/logbook", "ALPHA-PRIVATE")
    assert "STALE-COPY" not in _cached_text(page, "/pilot/logbook")
    context.close()


def test_pages_the_app_prefetches_are_saved_for_instant_screens(
    isolated_offline_server, traced_browser
):
    """The pilot pages the logbook page asks the browser to prefetch (never
    opened here) land in this login's saved pages, so their first visit is
    already instant."""
    _app, origin, _users, requests, _, _ = isolated_offline_server
    context = traced_browser.new_context(service_workers="allow")
    page = context.new_page()
    login(page, origin, "Alpha")  # ends on /pilot/logbook
    _quiet(page, requests)
    # The bottom bar's pages, and the other nav pages after them.
    for path in ("/flights/new", "/passports/", "/pilot/profile", "/pilot/tracks"):
        assert "</html>" in _cached_text(page, path), path
    context.close()


def test_a_blank_form_saved_on_another_day_opens_with_today(
    isolated_offline_server, traced_browser
):
    _app, origin, _users, requests, _, _ = isolated_offline_server
    context = traced_browser.new_context(service_workers="allow")
    page = context.new_page()
    login(page, origin, "Alpha")
    _wait_for_cached_text(page, "/flights/new", 'id="date"')
    _quiet(page, requests)
    # Pretend the saved form was rendered on another day.
    page.evaluate(
        r"""async () => {
          for (const name of (await caches.keys()).filter(n => n.includes('-private-'))) {
            const cache = await caches.open(name);
            const key = location.origin + '/flights/new';
            const old = await cache.match(key);
            if (!old) continue;
            const html = (await old.text()).replace(
              /(id="date" name="date"\s+value=")[0-9-]+/, '$12001-02-03');
            await cache.put(key, new Response(html, { status: 200, headers: old.headers }));
          }
        }"""
    )
    assert 'value="2001-02-03"' in _cached_text(page, "/flights/new")
    _boosted_visit(page, "/flights/new")
    today = page.evaluate(
        """() => { const d = new Date(); return d.getFullYear() + '-' +
             String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }"""
    )
    assert page.locator("#date").input_value() == today
    context.close()
