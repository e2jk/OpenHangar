#!/usr/bin/env python3
"""
Fetch strings flagged by Weblate quality checks and convert them to SARIF 2.1.0.

Weblate's public REST API only exposes a boolean `has_failing_check` per string —
it does not say *which* check(s) fired, or why. That detail is only rendered on
the string's own public translate page ("Things to check" panel). So this tool:

  1. If --languages is omitted, auto-discovers every language configured for
     the component (via Weblate's own translations list) — otherwise uses the
     given comma-separated list as-is.
  2. Queries the Weblate API for translated strings with a failing check, once
     per language.
  3. Fetches each flagged string's public translate page and scrapes the
     "Things to check" panel for the check name(s) and description(s).
  4. Emits one SARIF result per (string, check, source location) so GitHub Code
     Scanning can link every occurrence back to the file/line it came from.

No Weblate login is required for a public project, but an API token raises the
rate limit from 100 anonymous requests/day to 5000/hour — useful for scheduled
runs. Create one at <weblate-url>/accounts/profile/#api and pass it via
--token or the WEBLATE_API_TOKEN environment variable.

Usage:
    python3 weblate_checks_to_sarif.py --project myproj --component mycomp
    python3 weblate_checks_to_sarif.py --project myproj --component mycomp --languages en,fr,nl
    WEBLATE_API_TOKEN=wlu_xxx python3 weblate_checks_to_sarif.py --project myproj --component mycomp --languages fr

Severity mapping is deliberately conservative: checks that mean a translation
is actually malformed at render time (mismatched line breaks, printf/format
placeholders, XML markup) map to SARIF "warning"; everything else (reused
translation, unchanged translation, etc. — content-quality hints rather than
render-time breakage) maps to "note", the lowest SARIF level. This keeps
Weblate findings from ever outranking real security findings (CodeQL, Bandit,
...) in the same Code Scanning list. Override with --warning-checks.

Exit code is non-zero only on a real execution failure (bad token, rate
limit, network error) — a run that successfully finds flagged strings still
exits 0, since those are informational findings, not a build failure.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

USER_AGENT = (
    "weblate-checks-action/1.0 (+https://github.com/e2jk/weblate-checks-action)"
)

SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/"
    "Schemata/sarif-schema-2.1.0.json"
)

TOOL_NAME = "weblate-checks-action"
TOOL_INFO_URI = "https://github.com/e2jk/weblate-checks-action"
WEBLATE_CHECKS_DOCS_URL = "https://docs.weblate.org/en/latest/user/checks.html"

# Checks that mean a translation is malformed at render time, not just a
# content-quality hint — these map to SARIF "warning" instead of "note".
DEFAULT_WARNING_CHECKS = [
    "Mismatching line breaks",
    "Python format",
    "Python brace format",
    "C format",
    "C# format",
    "JavaScript format",
    "Perl format",
    "PHP format",
    "XML markup",
    "XML tags",
    "XML syntax",
]

Unit = dict[str, Any]
Check = tuple[str, str]  # (name, description)

# Matches one "Things to check" list-group-item block on a string's translate page:
#   <div class="list-group-item check check-item ">
#     <h5> ...icon svg... Reused translation <span class="check-number">...</span> </h5>
#     <p class="list-group-item-text check-description">Other source string: "Record"</p>
CHECK_ITEM_RE = re.compile(
    r'<div class="list-group-item check check-item\s*">'
    r"\s*<h5>(?P<name>.*?)</h5>"
    r'(?:\s*<p class="list-group-item-text check-description">(?P<desc>.*?)</p>)?',
    re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]+>")
SLUG_RE = re.compile(r"[^a-z0-9]+")


class ToolError(RuntimeError):
    """A known failure mode worth a clean, actionable message instead of a
    raw traceback (bad token, rate limit, network error)."""


class WeblateApiError(ToolError):
    """A Weblate API request failed."""


class RateLimitExceeded(WeblateApiError):
    """Weblate returned HTTP 429 — retrying won't help within a single run."""


def _log(msg: str) -> None:
    """Progress feedback on stderr — printed unconditionally since fetching
    checks is one HTTP request per flagged string and can take a while;
    silence for that long reads as a hang."""
    print(msg, file=sys.stderr, flush=True)


def _fetch(url: str, token: str | None) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Token {token}"
    req = urllib.request.Request(url, headers=headers)
    parsed = urllib.parse.urlparse(url)
    weblate_url = f"{parsed.scheme}://{parsed.netloc}"
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()  # type: ignore[no-any-return]
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                # Anonymous quota is 100/day, not a short burst window —
                # sleeping a few seconds and retrying is pointless.
                reset_header = (
                    exc.headers.get("X-RateLimit-Reset") if exc.headers else None
                )
                reset_note = ""
                if reset_header and reset_header.isdigit():
                    seconds = int(reset_header)
                    reset_note = (
                        f" Resets in ~{seconds // 3600}h{(seconds % 3600) // 60}m."
                    )
                token_hint = (
                    ""
                    if token
                    else " Set WEBLATE_API_TOKEN (5000 req/hour instead of "
                    "100 req/day anonymous) to avoid this."
                )
                raise RateLimitExceeded(
                    f"Weblate API rate limit hit (HTTP 429).{reset_note}{token_hint}"
                ) from exc
            if exc.code in (401, 403):
                raise WeblateApiError(
                    f"Weblate rejected the request (HTTP {exc.code}) — token is "
                    f"invalid or lacks access. Check it at "
                    f"{weblate_url}/accounts/profile/#api."
                ) from exc
            if exc.code == 503 and attempt == 0:
                time.sleep(5)
                continue
            raise
        except urllib.error.URLError as exc:
            raise WeblateApiError(f"Could not reach {weblate_url}: {exc}") from exc
    raise AssertionError("unreachable")  # pragma: no cover


def fetch_component_languages(
    weblate_url: str, project: str, component: str, token: str | None
) -> list[str]:
    """Auto-discover which languages are configured for a component, so
    callers don't have to hardcode or separately maintain a language list.
    Includes the source language: Weblate tracks a translation object for it
    too (checks run against it as well, e.g. "Reused translation" between two
    distinct source strings), even though it has no .po file of its own."""
    path = f"/api/components/{project}/{component}/translations/?page_size=100"
    codes: list[str] = []
    url: str | None = weblate_url + path
    while url:
        data = json.loads(_fetch(url, token))
        for translation in data["results"]:
            code = translation.get("language_code") or translation["language"]["code"]
            codes.append(code)
        url = data["next"]
    return codes


def fetch_flagged_units(
    weblate_url: str, project: str, component: str, language: str, token: str | None
) -> list[Unit]:
    query = (
        f"project:{project} AND component:{component} AND language:{language} "
        "AND has:check AND state:>=translated"
    )
    path = "/api/units/?" + urllib.parse.urlencode({"q": query, "page_size": 100})
    units: list[Unit] = []
    url: str | None = weblate_url + path
    while url:
        data = json.loads(_fetch(url, token))
        units.extend(data["results"])
        url = data["next"]
    return units


def scrape_checks(translate_url: str, token: str | None) -> list[Check]:
    try:
        page = _fetch(translate_url, token).decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, WeblateApiError):
        return []
    checks = []
    for m in CHECK_ITEM_RE.finditer(page):
        # Replace tags with a space, not "" — some check blocks (e.g. the
        # multi-language rollup shown on English source strings) rely on a
        # tag boundary for the visual gap, and stripping to "" would glue
        # adjacent words together ("Reused translationFrench, Dutch").
        name = _clean_html_text(m.group("name"))
        desc = _clean_html_text(m.group("desc") or "")
        checks.append((name, desc))
    return checks


def _clean_html_text(fragment: str) -> str:
    text = html.unescape(TAG_RE.sub(" ", fragment))
    return re.sub(r"\s+", " ", text).strip()


def _slugify(name: str) -> str:
    slug = SLUG_RE.sub("-", name.lower()).strip("-")
    return slug or "unknown-check"


def _parse_locations(location_field: str) -> list[tuple[str, int | None]]:
    """Weblate's `location` field is a comma-separated "file:line" list (or
    empty). Returns [] if nothing usable was found — the caller then emits a
    SARIF result with no physicalLocation rather than a fabricated one."""
    if not location_field:
        return []
    locations: list[tuple[str, int | None]] = []
    for token in location_field.split(","):
        token = token.strip()
        if not token:
            continue
        uri, sep, line_str = token.rpartition(":")
        if sep and line_str.isdigit():
            locations.append((uri, int(line_str)))
        else:
            locations.append((token, None))
    return locations


def _sarif_result(
    lang: str,
    unit: Unit,
    check_name: str,
    check_desc: str,
    level: str,
) -> dict[str, Any]:
    source = " / ".join(unit["source"])
    target = " / ".join(unit["target"])
    text = f'[{lang}] Source: "{source}"\nTarget: "{target}"'
    if check_desc:
        text += f"\n{check_desc}"
    text += f"\nEdit: {unit['web_url']}"

    result: dict[str, Any] = {
        "ruleId": _slugify(check_name),
        "level": level,
        "message": {"text": text},
    }

    locations = [
        {
            "physicalLocation": {
                "artifactLocation": {"uri": uri, "uriBaseId": "%SRCROOT%"},
                **({"region": {"startLine": line}} if line else {}),
            }
        }
        for uri, line in _parse_locations(unit.get("location", ""))
    ]
    if locations:
        result["locations"] = locations
    return result


def build_sarif(
    weblate_url: str,
    project: str,
    component: str,
    languages: list[str],
    token: str | None,
    delay: float,
    warning_checks: set[str],
    verbose: bool = False,
) -> tuple[dict[str, Any], int]:
    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    flagged_unit_ids: set[int] = set()

    for lang in languages:
        _log(f"[{lang}] querying Weblate for flagged strings...")
        units = fetch_flagged_units(weblate_url, project, component, lang, token)
        plural = "" if len(units) == 1 else "s"
        _log(
            f"[{lang}] {len(units)} flagged string{plural} — fetching check details..."
        )

        for i, unit in enumerate(units, start=1):
            checks = scrape_checks(unit["web_url"], token)
            if not checks:
                checks = [("Unknown check (could not parse — open link)", "")]
            if verbose:
                source_preview = " / ".join(unit["source"])[:60]
                check_names = ", ".join(name for name, _desc in checks)
                _log(f"[{lang}] {i}/{len(units)}: {source_preview!r} — {check_names}")
            time.sleep(delay)

            flagged_unit_ids.add(unit["id"])
            for name, desc in checks:
                slug = _slugify(name)
                if slug not in rules:
                    rules[slug] = {
                        "id": slug,
                        "name": re.sub(r"[^A-Za-z0-9]", "", name.title())
                        or "UnknownCheck",
                        "shortDescription": {"text": name},
                        "helpUri": WEBLATE_CHECKS_DOCS_URL,
                        "properties": {"tags": ["i18n", "translation"]},
                    }
                level = "warning" if name in warning_checks else "note"
                results.append(_sarif_result(lang, unit, name, desc, level))

    sarif = {
        "version": "2.1.0",
        "$schema": SARIF_SCHEMA,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "informationUri": TOOL_INFO_URI,
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
            }
        ],
    }
    return sarif, len(flagged_unit_ids)


def _write_github_output(pairs: dict[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in pairs.items():
            fh.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--weblate-url",
        default="https://hosted.weblate.org",
        help="Base URL of the Weblate instance (default: %(default)s)",
    )
    parser.add_argument("--project", required=True, help="Weblate project slug")
    parser.add_argument("--component", required=True, help="Weblate component slug")
    parser.add_argument(
        "--languages",
        default=None,
        help="Comma-separated language codes to check, e.g. 'en,fr,nl' "
        "(include the source language too — Weblate flags checks against "
        "it as well). If omitted, auto-discovers every language configured "
        "for the component from Weblate itself.",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("WEBLATE_API_TOKEN"),
        help="Weblate API token (default: $WEBLATE_API_TOKEN)",
    )
    parser.add_argument(
        "--output",
        default="weblate-checks.sarif",
        help="Path to write the SARIF file to (default: %(default)s)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.3,
        help="Seconds to sleep between per-string page fetches (default: %(default)s)",
    )
    parser.add_argument(
        "--warning-checks",
        default=",".join(DEFAULT_WARNING_CHECKS),
        help="Comma-separated Weblate check names that map to SARIF 'warning' "
        "instead of 'note' (default: format/markup checks that mean a "
        "translation is malformed at render time)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Also log each string's source text and detected check name(s) as they're fetched",
    )
    args = parser.parse_args()

    warning_checks = {c.strip() for c in args.warning_checks.split(",") if c.strip()}

    if args.token:
        _log("Using Weblate API token (rate limit: 5000 requests/hour).")
    else:
        _log(
            "No Weblate API token set — using anonymous access (rate limit: "
            "100 requests/day). Set --token or $WEBLATE_API_TOKEN for frequent runs."
        )

    try:
        if args.languages:
            languages = [
                lang.strip() for lang in args.languages.split(",") if lang.strip()
            ]
        else:
            _log(
                "No --languages given — auto-discovering configured languages "
                "from Weblate..."
            )
            languages = fetch_component_languages(
                args.weblate_url, args.project, args.component, args.token
            )
            _log(f"Discovered {len(languages)} language(s): {', '.join(languages)}")

        sarif, flagged_count = build_sarif(
            args.weblate_url,
            args.project,
            args.component,
            languages,
            args.token,
            args.delay,
            warning_checks,
            verbose=args.verbose,
        )
    except ToolError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted — no SARIF file written.", file=sys.stderr)
        return 130

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(sarif, fh, indent=2)

    result_count = len(sarif["runs"][0]["results"])
    _log(
        f"Wrote {result_count} result(s) across {flagged_count} flagged string(s) "
        f"to {args.output}"
    )
    _write_github_output(
        {"sarif-file": args.output, "flagged-count": str(flagged_count)}
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
