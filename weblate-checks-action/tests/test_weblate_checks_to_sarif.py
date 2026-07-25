import json
import urllib.error
from unittest.mock import patch

import pytest

import weblate_checks_to_sarif as wcs


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Reused translation", "reused-translation"),
        ("XML markup", "xml-markup"),
        ("  Weird!! Name??  ", "weird-name"),
        ("", "unknown-check"),
    ],
)
def test_slugify(name, expected):
    assert wcs._slugify(name) == expected


@pytest.mark.parametrize(
    "location,expected",
    [
        ("", []),
        (
            "app/templates/foo.html:12",
            [("app/templates/foo.html", 12)],
        ),
        (
            "app/templates/foo.html:12, app/templates/bar.html:34",
            [("app/templates/foo.html", 12), ("app/templates/bar.html", 34)],
        ),
        ("app/templates/foo.html", [("app/templates/foo.html", None)]),
        (" , app/templates/foo.html:5, ", [("app/templates/foo.html", 5)]),
    ],
)
def test_parse_locations(location, expected):
    assert wcs._parse_locations(location) == expected


def test_clean_html_text_strips_tags_and_collapses_whitespace():
    fragment = '<svg>icon</svg>  Reused   translation <span class="x">3</span>'
    assert wcs._clean_html_text(fragment) == "icon Reused translation 3"


def test_scrape_checks_parses_things_to_check_panel():
    page = """
    <div class="list-group-item check check-item ">
      <h5>Reused translation</h5>
      <p class="list-group-item-text check-description">Other source string: "Record"</p>
    </div>
    <div class="list-group-item check check-item ">
      <h5>Unchanged translation</h5>
    </div>
    """
    with patch.object(wcs, "_fetch", return_value=page.encode("utf-8")):
        checks = wcs.scrape_checks("https://example.org/translate/1", None)
    assert checks == [
        ("Reused translation", 'Other source string: "Record"'),
        ("Unchanged translation", ""),
    ]


def test_scrape_checks_returns_empty_on_fetch_failure():
    with patch.object(wcs, "_fetch", side_effect=wcs.WeblateApiError("boom")):
        assert wcs.scrape_checks("https://example.org/translate/1", None) == []


# ---------------------------------------------------------------------------
# _fetch error handling (mocking urllib.request.urlopen directly)
# ---------------------------------------------------------------------------


def _http_error(code, headers=None):
    return urllib.error.HTTPError(
        "https://example.org/api/units/", code, "err", headers or {}, None
    )


def test_fetch_raises_rate_limit_exceeded_on_429():
    with patch("urllib.request.urlopen", side_effect=_http_error(429)):
        with pytest.raises(wcs.RateLimitExceeded):
            wcs._fetch("https://example.org/api/units/", None)


def test_fetch_raises_weblate_api_error_on_403():
    with patch("urllib.request.urlopen", side_effect=_http_error(403)):
        with pytest.raises(wcs.WeblateApiError, match="rejected"):
            wcs._fetch("https://example.org/api/units/", "bad-token")


def test_fetch_raises_weblate_api_error_on_connection_failure():
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("no route to host"),
    ):
        with pytest.raises(wcs.WeblateApiError, match="Could not reach"):
            wcs._fetch("https://example.org/api/units/", None)


# ---------------------------------------------------------------------------
# fetch_flagged_units pagination
# ---------------------------------------------------------------------------


def test_fetch_flagged_units_follows_pagination():
    page1 = json.dumps(
        {"results": [{"id": 1}], "next": "https://example.org/api/units/?page=2"}
    ).encode()
    page2 = json.dumps({"results": [{"id": 2}], "next": None}).encode()

    with patch.object(wcs, "_fetch", side_effect=[page1, page2]):
        units = wcs.fetch_flagged_units(
            "https://example.org", "proj", "comp", "fr", None
        )
    assert [u["id"] for u in units] == [1, 2]


# ---------------------------------------------------------------------------
# fetch_component_languages (auto-discovery)
# ---------------------------------------------------------------------------


def test_fetch_component_languages_uses_top_level_language_code():
    page = json.dumps(
        {
            "results": [
                {"language_code": "en", "language": {"code": "should-not-be-used"}},
                {"language_code": "fr", "language": {"code": "should-not-be-used"}},
            ],
            "next": None,
        }
    ).encode()

    with patch.object(wcs, "_fetch", return_value=page):
        languages = wcs.fetch_component_languages(
            "https://example.org", "proj", "comp", None
        )
    assert languages == ["en", "fr"]


def test_fetch_component_languages_falls_back_to_nested_language_code():
    page = json.dumps(
        {"results": [{"language": {"code": "nl"}}], "next": None}
    ).encode()

    with patch.object(wcs, "_fetch", return_value=page):
        languages = wcs.fetch_component_languages(
            "https://example.org", "proj", "comp", None
        )
    assert languages == ["nl"]


def test_fetch_component_languages_follows_pagination():
    page1 = json.dumps(
        {
            "results": [{"language_code": "en"}],
            "next": "https://example.org/api/components/proj/comp/translations/?page=2",
        }
    ).encode()
    page2 = json.dumps({"results": [{"language_code": "fr"}], "next": None}).encode()

    with patch.object(wcs, "_fetch", side_effect=[page1, page2]):
        languages = wcs.fetch_component_languages(
            "https://example.org", "proj", "comp", None
        )
    assert languages == ["en", "fr"]


# ---------------------------------------------------------------------------
# build_sarif end-to-end (mocking the two network-facing functions)
# ---------------------------------------------------------------------------


def _unit(uid, source, target, location):
    return {
        "id": uid,
        "source": [source],
        "target": [target],
        "location": location,
        "web_url": f"https://example.org/translate/{uid}",
    }


def test_build_sarif_maps_severity_and_deduplicates_rules():
    units_by_lang = {
        "fr": [_unit(1, "Hello", "Bonjour", "app/templates/a.html:10")],
        "en": [_unit(2, "Hello", "Hi", "")],
    }
    checks_by_unit = {
        1: [("Reused translation", "Other source string")],
        2: [("Python format", "Mismatched %(name)s"), ("Reused translation", "")],
    }

    def fake_fetch(_weblate_url, _project, _component, language, _token):
        return units_by_lang[language]

    def fake_scrape(translate_url, _token):
        uid = int(translate_url.rsplit("/", 1)[-1])
        return checks_by_unit[uid]

    with (
        patch.object(wcs, "fetch_flagged_units", side_effect=fake_fetch),
        patch.object(wcs, "scrape_checks", side_effect=fake_scrape),
        patch("time.sleep"),
    ):
        sarif, flagged_count = wcs.build_sarif(
            "https://example.org",
            "proj",
            "comp",
            ["fr", "en"],
            None,
            0.0,
            set(wcs.DEFAULT_WARNING_CHECKS),
        )

    assert flagged_count == 2
    run = sarif["runs"][0]
    rule_ids = {r["id"] for r in run["tool"]["driver"]["rules"]}
    assert rule_ids == {"reused-translation", "python-format"}

    levels = {r["ruleId"]: r["level"] for r in run["results"]}
    assert levels["python-format"] == "warning"
    assert levels["reused-translation"] == "note"

    # The fr unit has a parseable location; the en unit's empty location
    # field must not fabricate a fake one.
    fr_result = next(r for r in run["results"] if "Bonjour" in r["message"]["text"])
    assert fr_result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == (
        "app/templates/a.html"
    )
    en_results = [r for r in run["results"] if '"Hi"' in r["message"]["text"]]
    assert all("locations" not in r for r in en_results)


def test_build_sarif_falls_back_to_unknown_check_when_scrape_finds_nothing():
    with (
        patch.object(
            wcs,
            "fetch_flagged_units",
            return_value=[_unit(1, "Hi", "Salut", "")],
        ),
        patch.object(wcs, "scrape_checks", return_value=[]),
        patch("time.sleep"),
    ):
        sarif, flagged_count = wcs.build_sarif(
            "https://example.org", "proj", "comp", ["fr"], None, 0.0, set()
        )

    assert flagged_count == 1
    [result] = sarif["runs"][0]["results"]
    assert result["ruleId"] == "unknown-check-could-not-parse-open-link"
    assert result["level"] == "note"


def test_write_github_output_appends_key_value_pairs(tmp_path, monkeypatch):
    out_file = tmp_path / "gh_output"
    out_file.write_text("existing=1\n")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))

    wcs._write_github_output(
        {"sarif-file": "weblate-checks.sarif", "flagged-count": "3"}
    )

    content = out_file.read_text()
    assert "existing=1" in content
    assert "sarif-file=weblate-checks.sarif" in content
    assert "flagged-count=3" in content


def test_write_github_output_is_a_noop_without_the_env_var(monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    wcs._write_github_output({"a": "b"})  # must not raise
