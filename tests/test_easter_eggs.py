"""
Tests for implemented Easter eggs.

Each class maps to one EE entry in docs/easter-eggs.md.
"""

# ── EE-07 — Browser Console Greeting ─────────────────────────────────────────

class TestConsoleGreeting:
    def test_console_script_present_on_every_page(self, app, client):
        """EE-07: every page that extends base.html carries the console greeting."""
        rv = client.get("/")
        assert b"EE-07" in rv.data
        assert b"console.log" in rv.data

    def test_greeting_contains_ascii_art(self, app, client):
        """EE-07: the greeting includes the plane silhouette."""
        rv = client.get("/")
        assert b"__|__" in rv.data
        assert b"---o--(_)--o---" in rv.data

    def test_greeting_contains_repo_url(self, app, client):
        """EE-07: the repo URL is embedded in the greeting."""
        rv = client.get("/")
        # repo_url is injected by the context processor; the default points to GitHub
        assert b"github.com" in rv.data.lower() or b"openhangar" in rv.data.lower()


# ── EE-08 — Secret Squawk URL Pages ──────────────────────────────────────────

class TestSquawkPages:
    def test_7700_returns_200(self, app, client):
        """EE-08: /squawk/7700 is reachable and returns HTTP 200."""
        rv = client.get("/squawk/7700")
        assert rv.status_code == 200

    def test_7700_contains_mayday(self, app, client):
        """EE-08: the 7700 page has a MAYDAY theme."""
        rv = client.get("/squawk/7700")
        assert b"MAYDAY" in rv.data

    def test_7500_returns_200(self, app, client):
        """EE-08: /squawk/7500 is reachable and returns HTTP 200."""
        rv = client.get("/squawk/7500")
        assert rv.status_code == 200

    def test_7500_contains_hijack_theme(self, app, client):
        """EE-08: the 7500 page has an unlawful-interference theme."""
        rv = client.get("/squawk/7500")
        assert b"7500" in rv.data
        assert b"REMAIN CALM" in rv.data

    def test_7600_returns_200(self, app, client):
        """EE-08: /squawk/7600 is reachable and returns HTTP 200."""
        rv = client.get("/squawk/7600")
        assert rv.status_code == 200

    def test_7600_contains_lost_comms_theme(self, app, client):
        """EE-08: the 7600 page has a NORDO / lost-comms theme."""
        rv = client.get("/squawk/7600")
        assert b"7600" in rv.data
        assert b"NORDO" in rv.data

    def test_1200_returns_200(self, app, client):
        """EE-08: /squawk/1200 is reachable and returns HTTP 200."""
        rv = client.get("/squawk/1200")
        assert rv.status_code == 200

    def test_1200_contains_us_vfr_theme(self, app, client):
        """EE-08: the 1200 page has a US VFR flavour (inHg altimeter)."""
        rv = client.get("/squawk/1200")
        assert b"1200" in rv.data
        assert b"VFR" in rv.data
        assert b"IN HG" in rv.data

    def test_7000_returns_200(self, app, client):
        """EE-08: /squawk/7000 is reachable and returns HTTP 200."""
        rv = client.get("/squawk/7000")
        assert rv.status_code == 200

    def test_7000_contains_european_vfr_theme(self, app, client):
        """EE-08: the 7000 page has a European VFR flavour (hPa QNH)."""
        rv = client.get("/squawk/7000")
        assert b"7000" in rv.data
        assert b"HPA" in rv.data

    def test_unknown_squawk_returns_404(self, app, client):
        """EE-08: an unlisted squawk code returns 404."""
        rv = client.get("/squawk/9999")
        assert rv.status_code == 404

    def test_pages_are_standalone(self, app, client):
        """EE-08: squawk pages have no navbar (no base.html chrome)."""
        for code in (7700, 7600, 7500, 7000, 1200):
            rv = client.get(f"/squawk/{code}")
            # base.html injects the EE-07 console script; standalone pages must not
            assert b"EE-07" not in rv.data
