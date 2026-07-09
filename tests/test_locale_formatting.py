"""
Locale negotiation and Babel formatting for every supported language.

The Docker image prunes Babel's CLDR locale-data down to en/fr/nl (plus
regional variants) — see docker/Dockerfile. These tests pin the behaviour
that pruning must preserve: browsers sending regional variants such as
fr-BE / nl-BE negotiate to a supported locale, and date/number formatting
works for every locale the app can serve.
"""

from datetime import date

import pytest  # pyright: ignore[reportMissingImports]
from babel import Locale  # pyright: ignore[reportMissingImports]
from babel.dates import format_date  # pyright: ignore[reportMissingImports]
from babel.numbers import format_decimal  # pyright: ignore[reportMissingImports]

from init import SUPPORTED_LOCALES  # pyright: ignore[reportMissingImports]


class TestBabelFormatting:
    @pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
    def test_supported_locales_format(self, locale):
        loc = Locale.parse(locale)
        assert format_date(date(2026, 7, 9), format="long", locale=loc)
        assert format_decimal(1234.5, locale=loc)

    @pytest.mark.parametrize("locale", ["fr_BE", "nl_BE", "en_GB"])
    def test_regional_variants_format(self, locale):
        """Regional variants a browser may send must keep working — the
        Dockerfile prune keeps en_*/fr_*/nl_* .dat files for this."""
        loc = Locale.parse(locale)
        assert format_date(date(2026, 7, 9), format="long", locale=loc)
        assert format_decimal(1234.5, locale=loc)


class TestAcceptLanguageNegotiation:
    @pytest.mark.parametrize(
        ("header", "marker"),
        [
            ("fr-BE,fr;q=0.9", b'lang="fr"'),
            ("nl-BE,nl;q=0.9", b'lang="nl"'),
            ("fr", b'lang="fr"'),
            ("nl", b'lang="nl"'),
            ("en", b'lang="en"'),
            ("de", b'lang="en"'),  # unsupported → default
        ],
    )
    def test_login_page_negotiates_locale(self, client, header, marker):
        resp = client.get(
            "/login", headers={"Accept-Language": header}, follow_redirects=True
        )
        assert resp.status_code == 200
        assert marker in resp.data
