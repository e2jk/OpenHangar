"""
Role-based access control E2E tests.

Verifies that authenticated users with insufficient role or aircraft access
are correctly rejected.  Runs against the dev-seeded stack.

Dev-seed roles:
  admin        admin@openhangar.dev   — Role.ADMIN,  all aircraft, TOTP enabled
  viewer       pierre@openhangar.dev  — Role.VIEWER, c172 only
  pilot        pilot@openhangar.dev   — Role.PILOT,  c172 + seminole
  maintenance  maintenance@…          — Role.MAINTENANCE, robin + jodel

Expected HTTP status codes:
  - Config/admin-only pages  → 403 (config blueprint before_request)
  - Aircraft not assigned     → 404 (user_can_access_aircraft returns False;
                                     404 is intentional — avoids leaking existence)
  - Write actions for viewer  → 403 (require_pilot_access decorator)
"""

import pytest

pytestmark = pytest.mark.e2e


# ── Helpers ────────────────────────────────────────────────────────────────────


def _login_no_totp(page, live_server_url: str, email: str, password: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.wait_for_load_state("networkidle")
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")


# ── Session-scoped authenticated contexts ─────────────────────────────────────


@pytest.fixture(scope="session")
def _pilot_context(browser_context, live_server_url):
    from dev_seed import _USERS

    email, password, *_ = _USERS[2]  # pilot@openhangar.dev
    ctx = browser_context.browser.new_context(
        base_url=live_server_url,
        ignore_https_errors=live_server_url.startswith("https://"),
    )
    pg = ctx.new_page()
    _login_no_totp(pg, live_server_url, email, password)
    pg.close()
    yield ctx
    ctx.close()


@pytest.fixture(scope="session")
def _viewer_context(browser_context, live_server_url):
    from dev_seed import _USERS

    email, password, *_ = _USERS[1]  # pierre@openhangar.dev (VIEWER)
    ctx = browser_context.browser.new_context(
        base_url=live_server_url,
        ignore_https_errors=live_server_url.startswith("https://"),
    )
    pg = ctx.new_page()
    _login_no_totp(pg, live_server_url, email, password)
    pg.close()
    yield ctx
    ctx.close()


@pytest.fixture
def pilot_page(_pilot_context):
    pg = _pilot_context.new_page()
    yield pg
    pg.close()


@pytest.fixture
def viewer_page(_viewer_context):
    pg = _viewer_context.new_page()
    yield pg
    pg.close()


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestRoleAccessControl:
    """Authenticated users with insufficient role/aircraft access are rejected."""

    # ── Admin-only config pages ───────────────────────────────────────────────

    def test_pilot_cannot_access_user_management(self, pilot_page, live_server_url):
        resp = pilot_page.goto(f"{live_server_url}/config/users/")
        assert resp.status == 403

    def test_viewer_cannot_access_user_management(self, viewer_page, live_server_url):
        resp = viewer_page.goto(f"{live_server_url}/config/users/")
        assert resp.status == 403

    def test_pilot_cannot_access_config_dashboard(self, pilot_page, live_server_url):
        resp = pilot_page.goto(f"{live_server_url}/config/")
        assert resp.status == 403

    # ── Aircraft-level access (returns 404, not 403 — avoids leaking existence) ─

    def test_pilot_cannot_access_unassigned_aircraft(
        self, pilot_page, live_server_url, seed
    ):
        # Robin (ac_del1) is assigned to maintenance, not pilot
        robin_id = seed["ac_del1"]
        resp = pilot_page.goto(f"{live_server_url}/aircraft/{robin_id}/flights")
        assert resp.status == 404

    def test_viewer_cannot_access_unassigned_aircraft(
        self, viewer_page, live_server_url, seed
    ):
        # Seminole (ac_stop) is not in the viewer's access list
        seminole_id = seed["ac_stop"]
        resp = viewer_page.goto(f"{live_server_url}/aircraft/{seminole_id}/flights")
        assert resp.status == 404

    # ── Write access (viewer is read-only) ────────────────────────────────────

    def test_viewer_cannot_open_new_flight_form(
        self, viewer_page, live_server_url, seed
    ):
        # /flights/new is protected by @require_pilot_access → 403 for VIEWER
        c172_id = seed["ac_flt"]
        resp = viewer_page.goto(f"{live_server_url}/flights/new?aircraft_id={c172_id}")
        assert resp.status == 403

    # ── Positive checks: authorised access still works ────────────────────────

    def test_pilot_can_access_assigned_aircraft(
        self, pilot_page, live_server_url, seed
    ):
        # c172 (ac_flt) is assigned to pilot → flights list must return 200
        c172_id = seed["ac_flt"]
        resp = pilot_page.goto(f"{live_server_url}/aircraft/{c172_id}/flights")
        assert resp.status == 200

    def test_viewer_can_access_assigned_aircraft(
        self, viewer_page, live_server_url, seed
    ):
        # c172 (ac_flt) is assigned to viewer → flights list must return 200
        c172_id = seed["ac_flt"]
        resp = viewer_page.goto(f"{live_server_url}/aircraft/{c172_id}/flights")
        assert resp.status == 200
