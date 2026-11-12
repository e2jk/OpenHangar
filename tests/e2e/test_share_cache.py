"""Exercise actual browser HTTP caching against a disposable showcase server."""

import threading
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture
def isolated_showcase_server(tmp_path, monkeypatch):
    from init import create_app
    from models import Aircraft, AircraftPhoto, ShareToken, Tenant, db
    from PIL import Image
    from werkzeug.serving import make_server

    monkeypatch.setenv(
        "OPENHANGAR_DATABASE_URL", f"sqlite:///{tmp_path / 'showcase.db'}"
    )
    monkeypatch.setenv("OPENHANGAR_SKIP_BACKGROUND_THREADS", "1")
    app = create_app()
    app.config.update(
        TESTING=True, UPLOAD_FOLDER=str(tmp_path), RATELIMIT_ENABLED=False
    )
    Image.new("RGB", (2, 2), "blue").save(tmp_path / "photo.jpg")
    with app.app_context():
        db.create_all()
        tenant = Tenant(name="Showcase cache test")
        db.session.add(tenant)
        db.session.flush()
        aircraft = Aircraft(
            tenant_id=tenant.id, registration="OO-CACHE", make="Cessna", model="172"
        )
        db.session.add(aircraft)
        db.session.flush()
        photo = AircraftPhoto(
            aircraft_id=aircraft.id,
            filename="photo.jpg",
            original_filename="photo.jpg",
            sort_order=1,
        )
        token = ShareToken(
            aircraft_id=aircraft.id, token="browsercache", access_level="showcase"
        )
        db.session.add_all([photo, token])
        db.session.commit()
        photo_id, token_id = photo.id, token.id
    requests = []

    @app.before_request
    def record_photo_request():
        from flask import request

        if request.endpoint == "share.showcase_photo":
            requests.append(request.path)

    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            app,
            f"http://127.0.0.1:{server.server_port}",
            photo_id,
            token_id,
            requests,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def test_showcase_photo_http_cache_rechecks_revoked_token(isolated_showcase_server):
    from models import ShareToken, db
    from playwright.sync_api import sync_playwright

    app, origin, photo_id, token_id, requests = isolated_showcase_server
    photo_path = f"/showcase/browsercache/photos/{photo_id}/img"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            # Routing/interception disables Chromium's HTTP cache: do not install
            # a page/context route here. The browser uses its ordinary cache.
            context = browser.new_context(service_workers="block")
            page = context.new_page()
            page.goto(origin + "/showcase/browsercache", wait_until="load")
            page.wait_for_function(
                "() => Array.from(document.images).some(img => img.naturalWidth === 2)"
            )
            before = len(requests)
            assert before >= 1
            with app.app_context():
                db.session.get(ShareToken, token_id).revoked_at = datetime.now(UTC)
                db.session.commit()
            status = page.evaluate(
                "path => fetch(path).then(response => response.status)", photo_path
            )
            assert status == 404
            assert len(requests) == before + 1
            context.close()
        finally:
            browser.close()
