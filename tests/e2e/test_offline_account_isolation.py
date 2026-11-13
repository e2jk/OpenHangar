"""Real service-worker/IndexedDB boundaries across accounts in one browser."""

import threading
import time
from datetime import date

import pytest

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
        page.reload(wait_until="load")
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
        # The forbidden page itself must evict its old cached success. The page's
        # own background snapshot refresh may get its 404 first and retire this
        # whole login (the stronger response); the worker then answers with the
        # generic offline page. Either way the private page is never served.
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
    page.goto(origin + "/pilot/logbook", wait_until="load")
    assert "/login" in page.url
    wait_until(
        page,
        "() => caches.keys().then(names => !names.some(n => n.includes('-private-')))",
    )
    assert page.evaluate("() => OhOffline.getPilotSnapshot()") is None

    wait_for_settled_worker(page)
    context.set_offline(True)
    for path in ("/pilot/logbook", "/pilot/logbook/offline"):
        page.goto(origin + path, wait_until="load")
        assert "ALPHA-PRIVATE" not in page.content()
    context.close()
