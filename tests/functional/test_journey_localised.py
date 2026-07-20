"""J17 — Localised journey smoke (docs/functional_test_plan.md).

Intent: a trimmed J1 (setup -> aircraft -> flight -> dashboard) renders
correctly in French and Dutch throughout, and a French page uses the
U+202F narrow no-break space convention documented for this project
(before `: ; ! ?`/`»`, and after `«`).

.mo files are gitignored build artifacts (compiled by CI/scripts/update_i18n.sh,
not committed) -- compiled here if absent, from the committed .po
sources, matching the plan's own "compile .mo in the fixture if absent"
instruction.

Locale persistence gotcha, worth documenting since it drove this test's
shape: the setup wizard doesn't log the user in until its last step, and
`_get_locale()` (app/init.py) prefers the logged-in user's DB `language`
column over `session["language"]` once a user_id is present. A plain
`session["language"] = "fr"` therefore only carries the pre-login wizard
pages -- surviving into the post-login dashboard/aircraft/flight pages
needs a real `GET /set-language/<lang>` call once logged in, which
persists to the DB column. Both are exercised here rather than relying
on the session key alone for the "whole journey" claim.

Existing: test_i18n.py/test_locale_formatting.py cover the switcher and
formatting helpers directly; chaining a full journey through in one
non-English locale is new.
"""

import os

import polib  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]

from tests.functional.conftest import log_flight, submit

_TRANSLATIONS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "app", "translations"
)

# msgid -> msgstr, verified directly against the committed .po sources.
_STRINGS = {
    "fr": {
        "wizard": "Configuration initiale",
        "welcome_flash": "Bienvenue dans OpenHangar !",
        "components": "Composants",
        "logbook": "Carnet de vol cellule",
    },
    "nl": {
        "wizard": "Eerste configuratie",
        "welcome_flash": "Welkom bij OpenHangar!",
        "components": "Onderdelen",
        "logbook": "Vliegtuiglogboek",
    },
}


def _ensure_mo_compiled(lang: str) -> None:
    base = os.path.join(_TRANSLATIONS_DIR, lang, "LC_MESSAGES")
    po_path = os.path.join(base, "messages.po")
    mo_path = os.path.join(base, "messages.mo")
    if not os.path.exists(mo_path):
        polib.pofile(po_path).save_as_mofile(mo_path)


@pytest.mark.parametrize("locale", ("fr", "nl"))
def test_localised_journey(app, client, locale):
    _ensure_mo_compiled(locale)
    strings = _STRINGS[locale]

    # Pre-login: no user yet, so session["language"] is what _get_locale()
    # reads.
    with client.session_transaction() as sess:
        sess["language"] = locale

    wizard_page = client.get("/setup")
    assert strings["wizard"].encode("utf-8") in wizard_page.data

    email = f"owner-{locale}@example.com"
    submit(
        client,
        "/setup",
        {
            "step": "account",
            "email": email,
            "password": "SuperSecret123!",
            "name": "Owner",
        },
    )
    submit(client, "/setup", {"step": "totp", "action": "skip"})
    submit(
        client,
        "/setup",
        {"step": "operating_model", "operating_model": "sole_operator"},
    )
    # Logs the user in and flashes the welcome message on the redirect target.
    dashboard = submit(
        client, "/setup", {"step": "aircraft_count", "aircraft_count": "1"}
    )
    assert strings["welcome_flash"].encode("utf-8") in dashboard.data
    if locale == "fr":
        assert " !".encode() in dashboard.data

    # Post-login: session["language"] alone would no longer win (see module
    # docstring) -- persist to the DB via the real route.
    client.get(f"/set-language/{locale}", follow_redirects=True)

    resp = client.post(
        "/aircraft/new",
        data={"registration": "OO-TST", "make": "Cessna", "model": "172S"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # Redirect lands on /aircraft/<registration> (AircraftRefConverter) —
    # look the row up directly rather than depending on the URL's shape.
    with app.app_context():
        from models import Aircraft  # pyright: ignore[reportMissingImports]

        aircraft_id = Aircraft.query.filter_by(registration="OO-TST").first().id

    aircraft_page = client.get(f"/aircraft/{aircraft_id}")
    assert strings["components"].encode("utf-8") in aircraft_page.data

    log_flight(client, app, aircraft_id)

    logbook_page = client.get(f"/aircraft/{aircraft_id}/flights")
    assert strings["logbook"].encode("utf-8") in logbook_page.data
