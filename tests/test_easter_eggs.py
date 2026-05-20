"""
Tests for implemented Easter eggs.

Each class maps to one EE entry in docs/easter-eggs.md.
"""

import init as _init

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


# ── EE-09 — Aviation History Day Banner ──────────────────────────────────────


class TestAviationDayBanner:
    def test_no_banner_on_ordinary_day(self):
        """EE-09: returns None on a day with no aviation event."""
        assert _init._aviation_day_msgid(1, 1) is None
        assert _init._aviation_day_msgid(6, 15) is None

    def test_concorde_march_2(self):
        """EE-09: 2 March → Concorde first flight."""
        msgid = _init._aviation_day_msgid(3, 2)
        assert msgid is not None
        assert "Concorde" in msgid

    def test_lindbergh_may_21(self):
        """EE-09: 21 May → Lindbergh transatlantic."""
        msgid = _init._aviation_day_msgid(5, 21)
        assert msgid is not None
        assert "Lindbergh" in msgid

    def test_bleriot_july_25(self):
        """EE-09: 25 July → Blériot Channel crossing."""
        msgid = _init._aviation_day_msgid(7, 25)
        assert msgid is not None
        assert "Blériot" in msgid

    def test_pilatre_november_21(self):
        """EE-09: 21 November → Pilâtre de Rozier balloon flight."""
        msgid = _init._aviation_day_msgid(11, 21)
        assert msgid is not None
        assert "Rozier" in msgid

    def test_wright_december_17(self):
        """EE-09: 17 December → Wright Brothers first flight."""
        msgid = _init._aviation_day_msgid(12, 17)
        assert msgid is not None
        assert "Wright" in msgid

    def test_all_five_events_covered(self):
        """EE-09: _AVIATION_DAYS has exactly the expected five entries."""
        dates = {(m, d) for m, d, _ in _init._AVIATION_DAYS}
        assert dates == {(3, 2), (5, 21), (7, 25), (11, 21), (12, 17)}


# ── EE-06 — NVG Mode ──────────────────────────────────────────────────────────


class TestNvgMode:
    def test_nvg_css_rule_present(self, app, client):
        """EE-06: the NVG filter rule is shipped in base.css."""
        rv = client.get("/static/css/base.css")
        assert b"nvg-mode" in rv.data
        assert b"saturate" in rv.data

    def test_nvg_js_present(self, app, client):
        """EE-06: the EE-06 JS block is present in base.html."""
        rv = client.get("/")
        assert b"EE-06" in rv.data
        assert b"oh-nvg" in rv.data
        assert b"nvg-mode" in rv.data

    def test_nvg_trigger_on_brand(self, app, client):
        """EE-06: the handler targets the navbar brand, not a separate button."""
        rv = client.get("/")
        assert b"navbar-brand" in rv.data
        assert b"nvg-toggle" not in rv.data


# ── EE-04 — Logo Click Sequence ───────────────────────────────────────────────


class TestLogoClickSequence:
    def test_secret_page_returns_200(self, app, client):
        """EE-04: /hangar/secret is reachable and returns HTTP 200."""
        rv = client.get("/hangar/secret")
        assert rv.status_code == 200

    def test_secret_page_is_standalone(self, app, client):
        """EE-04: the secret page has no navbar chrome."""
        rv = client.get("/hangar/secret")
        assert b"EE-07" not in rv.data

    def test_secret_page_contains_caption(self, app, client):
        """EE-04: the page contains the easter-egg caption."""
        rv = client.get("/hangar/secret")
        assert b"unrestricted airspace" in rv.data

    def test_secret_page_contains_hangar_art(self, app, client):
        """EE-04: the page includes ASCII hangar art."""
        rv = client.get("/hangar/secret")
        assert b"O P E N" in rv.data
        assert b"H A N G A R" in rv.data

    def test_secret_page_contains_biplane(self, app, client):
        """EE-04: the page includes a biplane taxi element."""
        rv = client.get("/hangar/secret")
        assert b"biplane" in rv.data
        assert b"taxi" in rv.data

    def test_ee04_js_present(self, app, client):
        """EE-04: the click-sequence JS is embedded in base.html."""
        rv = client.get("/")
        assert b"EE-04" in rv.data
        assert b"/hangar/secret" in rv.data

    def test_ee04_targets_navbar(self, app, client):
        """EE-04: the JS targets the full navbar, not a sub-element."""
        rv = client.get("/")
        assert b"nav.navbar" in rv.data
