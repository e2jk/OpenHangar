"""Tests for Phase 19/19b: i18n infrastructure — language switcher, locale selector,
locale-aware date formatting, and translation completeness."""

import os

import bcrypt  # pyright: ignore[reportMissingImports]
import polib  # pyright: ignore[reportMissingImports]
from datetime import date

import pytest  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    PilotLogbookEntry,
    PilotProfile,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_user(app, email="i18n@example.com", language="en"):
    with app.app_context():
        tenant = Tenant(name="i18n Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
            is_active=True,
            language=language,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        db.session.commit()
        return user.id


def _login(app, client, email="i18n@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _add_logbook_entry(app, uid, entry_date):
    with app.app_context():
        db.session.add(
            PilotLogbookEntry(
                pilot_user_id=uid,
                date=entry_date,
                single_pilot_se=1.5,
                function_pic=1.5,
                landings_day=1,
            )
        )
        db.session.commit()


# ── Language switcher ─────────────────────────────────────────────────────────


class TestLanguageSwitcher:
    def test_set_language_saves_to_db(self, app, client):
        uid = _create_user(app, language="en")
        _login(app, client)
        resp = client.get("/set-language/fr", follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            user = db.session.get(User, uid)
            assert user.language == "fr"

    def test_set_language_switches_back_to_en(self, app, client):
        uid = _create_user(app, language="fr")
        _login(app, client)
        client.get("/set-language/en", follow_redirects=True)
        with app.app_context():
            user = db.session.get(User, uid)
            assert user.language == "en"

    def test_set_language_rejects_unknown_locale(self, app, client):
        _create_user(app)
        _login(app, client)
        resp = client.get("/set-language/xx")
        assert resp.status_code == 400

    def test_set_language_valid_next_redirects_to_path(self, app, client):
        # A safe relative ?next= path is honoured.
        _create_user(app)
        _login(app, client)
        resp = client.get("/set-language/fr?next=/aircraft", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/aircraft"

    def test_set_language_preserves_query_string_in_next(self, app, client):
        # ?next= with a query string (e.g. aircraft list with context) is preserved.
        _create_user(app)
        _login(app, client)
        resp = client.get(
            "/set-language/fr?next=/aircraft/?next=log_flight", follow_redirects=False
        )
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/aircraft/?next=log_flight"

    def test_set_language_rejects_external_next(self, app, client):
        # Absolute URL in ?next= → falls back to "/".
        _create_user(app)
        _login(app, client)
        resp = client.get(
            "/set-language/fr?next=http://evil.com/steal", follow_redirects=False
        )
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"

    def test_set_language_rejects_protocol_relative_next(self, app, client):
        # //evil.com/steal → path starts with "//" → falls back to "/".
        _create_user(app)
        _login(app, client)
        resp = client.get(
            "/set-language/fr?next=//evil.com/steal", follow_redirects=False
        )
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"

    def test_set_language_empty_next_redirects_to_root(self, app, client):
        # No ?next= → falls back to "/".
        _create_user(app)
        _login(app, client)
        resp = client.get("/set-language/fr", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"

    def test_set_language_unauthenticated_does_not_crash(self, app, client):
        resp = client.get("/set-language/fr", follow_redirects=True)
        assert resp.status_code == 200

    def test_set_language_stale_user_id_saves_to_session(self, app, client):
        # Simulate a stale user_id in the session (e.g. DB wiped between logins)
        # but no matching user in DB — language should fall back to session storage.
        with client.session_transaction() as sess:
            sess["user_id"] = 99999  # non-existent user
        resp = client.get("/set-language/fr", follow_redirects=True)
        assert resp.status_code == 200
        with client.session_transaction() as sess:
            assert sess.get("language") == "fr"

    def test_language_persists_across_requests(self, app, client):
        uid = _create_user(app, language="en")
        _login(app, client)
        client.get("/set-language/fr", follow_redirects=False)
        with app.app_context():
            user = db.session.get(User, uid)
            assert user.language == "fr"
        # Confirm it persists to a second DB read
        with app.app_context():
            user2 = db.session.get(User, uid)
            assert user2.language == "fr"


# ── Locale selector ───────────────────────────────────────────────────────────


class TestLocaleSelector:
    def test_user_language_preference_applied(self, app, client):
        # User with language='fr' → logbook dates in French
        uid = _create_user(app, email="fr@example.com", language="fr")
        _login(app, client, "fr@example.com")
        # Add an entry with a date whose French month name is unambiguous (mai, juin…)
        _add_logbook_entry(app, uid, date(2026, 5, 6))
        with app.app_context():
            db.session.add(PilotProfile(user_id=uid))
            db.session.commit()
        resp = client.get("/pilot/logbook")
        assert resp.status_code == 200
        assert b"mai" in resp.data.lower()  # French: "6 mai 2026"

    def test_english_locale_default(self, app, client):
        uid = _create_user(app, email="en@example.com", language="en")
        _login(app, client, "en@example.com")
        _add_logbook_entry(app, uid, date(2026, 5, 6))
        with app.app_context():
            db.session.add(PilotProfile(user_id=uid))
            db.session.commit()
        resp = client.get("/pilot/logbook")
        assert resp.status_code == 200
        assert b"May" in resp.data  # English: "06 May 2026"

    def test_accept_language_fallback_for_unauthenticated(self, app, client):
        resp = client.get("/", headers={"Accept-Language": "fr,en;q=0.9"})
        # Dashboard redirects to welcome or landing for unauthenticated users
        # but the request is processed with the fr locale
        assert resp.status_code in (200, 302)
        # Verify the html lang attribute is set by checking the context:
        # visit any page and check lang attribute if 200
        resp2 = client.get(
            "/", headers={"Accept-Language": "fr"}, follow_redirects=True
        )
        assert b'lang="fr"' in resp2.data

    def test_language_switcher_ui_present_when_logged_in(self, app, client):
        _create_user(app, email="sw@example.com")
        _login(app, client, "sw@example.com")
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"set-language/en" in resp.data
        assert b"set-language/fr" in resp.data


# ── Locale-aware date formatting ──────────────────────────────────────────────


class TestDateFormatting:
    def _logbook_with_may_entry(self, app, client, email, language):
        uid = _create_user(app, email=email, language=language)
        _login(app, client, email)
        _add_logbook_entry(app, uid, date(2026, 5, 6))
        with app.app_context():
            db.session.add(PilotProfile(user_id=uid))
            db.session.commit()
        return client.get("/pilot/logbook")

    def test_format_date_english_month_name(self, app, client):
        resp = self._logbook_with_may_entry(app, client, "date_en@example.com", "en")
        assert b"May" in resp.data

    def test_format_date_french_month_name(self, app, client):
        resp = self._logbook_with_may_entry(app, client, "date_fr@example.com", "fr")
        assert b"mai" in resp.data.lower()

    def test_format_date_year_present(self, app, client):
        resp = self._logbook_with_may_entry(app, client, "date_yr@example.com", "en")
        assert b"2026" in resp.data

    def test_html_lang_attribute_set_en(self, app, client):
        _create_user(app, email="hlang_en@example.com", language="en")
        _login(app, client, "hlang_en@example.com")
        resp = client.get("/")
        assert b'lang="en"' in resp.data

    def test_html_lang_attribute_set_fr(self, app, client):
        _create_user(app, email="hlang_fr@example.com", language="fr")
        _login(app, client, "hlang_fr@example.com")
        resp = client.get("/")
        assert b'lang="fr"' in resp.data


# ── Translation completeness ───────────────────────────────────────────────────

_TRANSLATIONS_DIR = os.path.join(os.path.dirname(__file__), "../app/translations")


def _po_files():
    """Return (lang, path) for every committed .po file."""
    results = []
    for lang in os.listdir(_TRANSLATIONS_DIR):
        path = os.path.join(_TRANSLATIONS_DIR, lang, "LC_MESSAGES", "messages.po")
        if os.path.isfile(path):
            results.append((lang, path))
    return sorted(results)


class TestTranslationCompleteness:
    @pytest.mark.parametrize("lang,po_path", _po_files())
    def test_no_untranslated_entries(self, lang, po_path):
        po = polib.pofile(po_path)
        bad = po.untranslated_entries()
        assert bad == [], (
            f"{len(bad)} untranslated {lang} strings — translate them and commit messages.po:\n"
            + "\n".join(f"  {e.msgid!r}" for e in bad[:10])
        )

    @pytest.mark.parametrize("lang,po_path", _po_files())
    def test_no_fuzzy_entries(self, lang, po_path):
        po = polib.pofile(po_path)
        bad = po.fuzzy_entries()
        assert bad == [], (
            f"{len(bad)} fuzzy {lang} entries — review and remove #, fuzzy markers:\n"
            + "\n".join(f"  {e.msgid!r}" for e in bad[:10])
        )

    @pytest.mark.parametrize("lang,po_path", _po_files())
    def test_no_multiline_msgstr_without_continuation(self, lang, po_path):
        """Guard against msgstr '' lines not followed by a continuation.

        A valid multiline translation opens with ``msgstr ""`` and continues
        on the next line with ``"content..."``.  When restoration scripts write
        a one-line value for an entry that was previously stored as multiline,
        the continuation can accidentally get dropped, leaving a bare
        ``msgstr ""`` that looks non-empty to polib (because the *old*
        continuation is gone) but is actually untranslated.  This raw-text
        check catches that before polib's parser has a chance to mask it.
        """
        with open(po_path, encoding="utf-8") as f:
            lines = f.readlines()

        bad = []
        for i, line in enumerate(lines):
            if line.strip() != 'msgstr ""':
                continue
            # Find the next non-blank line after this msgstr ""
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines) and lines[j].strip().startswith('"'):
                continue  # valid multiline opener — continuation present
            # Standalone msgstr "" — only the PO header (msgid "") is allowed
            msgid_line = ""
            for k in range(i - 1, max(i - 10, -1), -1):
                if lines[k].startswith("msgid "):
                    msgid_line = lines[k].strip()
                    break
            if msgid_line != 'msgid ""':
                bad.append(
                    f"  line {i + 1}: standalone empty msgstr (no continuation) for {msgid_line!r}"
                )

        assert bad == [], (
            f'{len(bad)} {lang} entries have msgstr "" with no continuation — '
            "translation may have been dropped by a restore script:\n"
            + "\n".join(bad[:10])
        )
