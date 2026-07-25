#!/usr/bin/env python3
"""
Fetch strings with failing Weblate quality checks and write a Markdown review report.

Weblate's public REST API only exposes a boolean `has_failing_check` per string —
it does not say *which* check(s) fired, or why. That detail is only rendered on the
string's own public translate page ("Things to check" panel). So this script:

  1. Queries the Weblate API for translated strings with a failing check, once per
     locale in SUPPORTED_LOCALES from app/init.py — including English: it's the
     source language, but Weblate still flags checks against it (e.g. two distinct
     English strings both translated the same way elsewhere), so it needs the same
     scrape. Adding a new supported language picks it up automatically, no script
     changes needed.
  2. Fetches each flagged string's public translate page and scrapes the "Things to
     check" panel for the check name(s) and description.
  3. Writes a Markdown report grouped by language, then by check type, with the
     source/target strings, source locations, and a direct Weblate edit link —
     suitable for reviewing by hand or handing to an AI coding assistant. Strings
     that share a translation but whose English source differs only by
     capitalization (e.g. "Aircraft Type" / "Aircraft type") get their own
     "Case-variant duplicates" section, since that's almost always the same string
     accidentally duplicated rather than a real translation issue.
  4. Also writes a <output>.json cache of the raw fetched data next to the Markdown
     report, so a later run can use --recheck (see below) instead of querying
     Weblate again.

No Weblate login is required for any of this (the project is public), but an API
token raises the rate limit from 100 anonymous requests/day to 5000/hour — useful
if this is run often. Create one at https://hosted.weblate.org/accounts/profile/#api
and pass it via --token, the WEBLATE_API_TOKEN environment variable, or a
WEBLATE_API_TOKEN=... line in a (gitignored) .env file at the repo root.

Usage:
    python3 scripts/weblate_check_report.py
    python3 scripts/weblate_check_report.py --languages fr,nl --output /tmp/report.md
    WEBLATE_API_TOKEN=wlu_xxx python3 scripts/weblate_check_report.py
    python3 scripts/weblate_check_report.py --verbose
    python3 scripts/weblate_check_report.py --recheck

Progress is logged to stderr as it runs (one HTTP request per flagged string,
so this can take a while) — pass --verbose for each string's source text and
detected check name(s) as well.

--recheck: once you've started fixing strings locally (editing .po files, merging
duplicate source strings, etc.) but haven't pushed/synced with Weblate yet, a normal
run would just show the same stale results — Weblate can't know about local,
unpushed changes. --recheck skips Weblate entirely and instead re-derives the report
from the last cached run (<output>.json) by checking, for each previously-flagged
string, whether it's still present with the same content in the *current* local
.po files (fr/nl) or extractable source strings (English, via a fresh pybabel
extract) — dropping anything that's already been fixed locally. Requires having run
the script at least once without --recheck first.

Caveat: check names/descriptions are scraped from Weblate's HTML, not a stable API
contract. If Weblate changes that page's markup, scraping degrades gracefully (the
string is still reported, just without a parsed check name) rather than failing.
"""

from __future__ import annotations

import argparse
import ast
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import polib

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBLATE_URL = "https://hosted.weblate.org"
USER_AGENT = "OpenHangar-i18n-check-report/1.0 (+https://github.com/e2jk/OpenHangar)"

Unit = dict[str, Any]
Check = tuple[str, str]
Entry = tuple[Unit, list[Check]]

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


def _dotenv_token(var: str = "WEBLATE_API_TOKEN") -> str | None:
    """Best-effort fallback: read VAR from a .env file at the repo root.

    Not a general .env loader — just enough to avoid adding python-dotenv as a
    dependency for a single optional value. `.env` is already gitignored.
    """
    dotenv = REPO_ROOT / ".env"
    if not dotenv.is_file():
        return None
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == var:
            return value.strip().strip("'\"")
    return None


def supported_locales() -> list[str]:
    """All locales Weblate tracks checks for, including English.

    English is the source language, but Weblate still runs (and flags) checks
    against it — e.g. two distinct English strings translated identically
    elsewhere is reported as a "Reused translation" check on the English
    source unit itself, not just on the fr/nl translations of it.
    """
    init_py = (REPO_ROOT / "app" / "init.py").read_text(encoding="utf-8")
    match = re.search(r"^SUPPORTED_LOCALES\s*=\s*(\[.*?\])", init_py, re.MULTILINE)
    if not match:
        raise RuntimeError("Could not find SUPPORTED_LOCALES in app/init.py")
    locales: list[str] = ast.literal_eval(match.group(1))
    return locales


class ReportError(RuntimeError):
    """A known failure mode worth a clean, actionable message instead of a raw
    traceback (bad token, rate limit, missing --recheck cache, etc.)."""


class WeblateApiError(ReportError):
    """A Weblate API request failed."""


class RateLimitExceeded(WeblateApiError):
    """Weblate returned HTTP 429 — retrying won't help within a single run."""


def _fetch(url: str, token: str | None) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Token {token}"
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()  # type: ignore[no-any-return]
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                # Anonymous quota is 100/day, not a short burst window — sleeping
                # a few seconds and retrying is pointless, so fail fast with a
                # message that says when it resets instead of a raw traceback.
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
                    f"Weblate rejected the request (HTTP {exc.code}) — "
                    "WEBLATE_API_TOKEN is set but invalid or lacks access. Check it "
                    "at https://hosted.weblate.org/accounts/profile/#api."
                ) from exc
            if exc.code == 503 and attempt == 0:
                time.sleep(5)
                continue
            raise
    raise AssertionError("unreachable")  # pragma: no cover


def fetch_flagged_units(
    project: str, component: str, language: str, token: str | None
) -> list[Unit]:
    query = (
        f"project:{project} AND component:{component} AND language:{language} "
        "AND has:check AND state:>=translated"
    )
    path = "/api/units/?" + urllib.parse.urlencode({"q": query, "page_size": 100})
    units: list[Unit] = []
    url: str | None = WEBLATE_URL + path
    while url:
        data = json.loads(_fetch(url, token))
        units.extend(data["results"])
        url = data["next"]
    return units


def scrape_checks(translate_url: str, token: str | None) -> list[Check]:
    try:
        page = _fetch(translate_url, token).decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError):
        return []
    checks = []
    for m in CHECK_ITEM_RE.finditer(page):
        # Replace tags with a space, not "" — some check blocks (e.g. the
        # multi-language rollup shown on English source strings) rely on a tag
        # boundary for the visual gap, and stripping to "" would glue adjacent
        # words together ("Reused translationFrench, Dutch").
        name = _clean_html_text(m.group("name"))
        desc = _clean_html_text(m.group("desc") or "")
        checks.append((name, desc))
    return checks


def _clean_html_text(fragment: str) -> str:
    text = html.unescape(TAG_RE.sub(" ", fragment))
    return re.sub(r"\s+", " ", text).strip()


def _log(msg: str) -> None:
    """Progress feedback on stderr — printed unconditionally (not just --verbose)
    since fetching checks is one HTTP request per flagged string and can take a
    while; silence for that long reads as a hang."""
    print(msg, file=sys.stderr, flush=True)


_CURSOR_UP_AND_CLEAR = "\033[F\033[K"  # move to start of previous line, clear it


def _bar(done: int, total: int, width: int = 24) -> str:
    filled = width if total == 0 else round(width * done / total)
    pct = 100 if total == 0 else int(100 * done / total)
    return "[" + "=" * filled + "-" * (width - filled) + f"] {done}/{total} ({pct}%)"


class _Progress:
    """Redraws a one-line progress bar in place on a real terminal.

    In verbose mode, detail lines are printed above the bar and stay in the
    scrollback — only the bar line itself gets erased and redrawn each time,
    so it stays pinned as the last line on screen. Falls back to plain
    sequential lines (no cursor control codes) when stderr isn't a TTY, e.g.
    redirected to a file or captured by CI — control codes would just show up
    as garbage in a log.
    """

    def __init__(self, lang: str, total: int, *, verbose: bool) -> None:
        self.lang = lang
        self.total = total
        self.verbose = verbose
        self.is_tty = sys.stderr.isatty()
        self._drawn = False

    def update(self, done: int, detail: str = "") -> None:
        bar_line = f"[{self.lang}] {_bar(done, self.total)}"
        if not self.is_tty:
            _log(f"{bar_line}: {detail}" if detail else bar_line)
            return
        if self.verbose:
            if self._drawn:
                sys.stderr.write(_CURSOR_UP_AND_CLEAR)
            sys.stderr.write(f"[{self.lang}] {detail}\n{bar_line}\n")
        else:
            sys.stderr.write(f"\r\033[K{bar_line}")
        sys.stderr.flush()
        self._drawn = True

    def finish(self) -> None:
        if self.is_tty and self._drawn and not self.verbose:
            sys.stderr.write("\n")
            sys.stderr.flush()


def _case_variant_groups(
    entries: list[Entry],
) -> tuple[list[tuple[str, list[Entry]]], list[Entry]]:
    """Split entries into (case-variant duplicate groups, remaining entries).

    A case-variant group is 2+ flagged strings sharing the same target
    translation, whose English sources are identical once lowercased but not
    verbatim — e.g. "Aircraft Type" / "Aircraft type" both translating to
    "Vliegtuigtype". Almost always the same concept accidentally duplicated by
    capitalization, so it's worth surfacing separately from generic "Reused
    translation" noise (which these also always trigger).
    """
    by_target: dict[str, list[Entry]] = {}
    for unit, checks in entries:
        by_target.setdefault(" / ".join(unit["target"]), []).append((unit, checks))

    groups: list[tuple[str, list[Entry]]] = []
    consumed: set[int] = set()
    for target, group in by_target.items():
        if len(group) < 2:
            continue
        sources = [" / ".join(u["source"]) for u, _c in group]
        if len(set(sources)) > 1 and len({s.lower() for s in sources}) == 1:
            groups.append((target, group))
            consumed.update(u["id"] for u, _c in group)

    remaining = [(u, c) for u, c in entries if u["id"] not in consumed]
    groups.sort(key=lambda g: g[0].lower())
    return groups, remaining


def _render_language_section(lang: str, entries: list[Entry]) -> list[str]:
    plural = "" if len(entries) == 1 else "s"
    body = [f"## {lang} — {len(entries)} flagged string{plural}", ""]
    if not entries:
        body += ["_No flagged strings._", ""]
        return body

    case_groups, remaining = _case_variant_groups(entries)

    if case_groups:
        body.append(f"### Case-variant duplicates ({len(case_groups)})")
        body.append("")
        body.append(
            "_Same translation; English sources differ only by capitalization — "
            "likely the same concept duplicated, consider standardizing on one._"
        )
        body.append("")
        for target, group in case_groups:
            body.append(f"- Target: `{target}`")
            for unit, _checks in group:
                source = " / ".join(unit["source"])
                body.append(f"  - Source: `{source}` — {unit['location']}")
                body.append(f"    Edit: {unit['web_url']}")
            body.append("")

    by_check: dict[str, list[Entry]] = {}
    for unit, checks in remaining:
        key = checks[0][0] if checks else "Unknown check (could not parse — open link)"
        by_check.setdefault(key, []).append((unit, checks))

    for check_name in sorted(by_check):
        group = by_check[check_name]
        body.append(f"### {check_name} ({len(group)})")
        body.append("")
        for unit, checks in group:
            source = " / ".join(unit["source"])
            target = " / ".join(unit["target"])
            desc = next((d for n, d in checks if n == check_name and d), "")
            body.append(f"- Source: `{source}`")
            body.append(f"  Target: `{target}`")
            if desc:
                body.append(f"  Note: {desc}")
            body.append(f"  Location: {unit['location']}")
            body.append(f"  Edit: {unit['web_url']}")
            body.append("")

    return body


def build_report(
    project: str,
    component: str,
    languages: list[str],
    token: str | None,
    delay: float,
    verbose: bool = False,
) -> tuple[str, dict[str, Any]]:
    total = 0
    body: list[str] = []
    cache_languages: dict[str, list[dict[str, Any]]] = {}

    for lang in languages:
        _log(f"[{lang}] querying Weblate for flagged strings...")
        units = fetch_flagged_units(project, component, lang, token)
        plural = "" if len(units) == 1 else "s"
        _log(
            f"[{lang}] {len(units)} flagged string{plural} — fetching check details..."
        )

        entries: list[Entry] = []
        progress = _Progress(lang, len(units), verbose=verbose)
        for i, unit in enumerate(units, start=1):
            checks = scrape_checks(unit["web_url"], token)
            detail = ""
            if verbose:
                source_preview = " / ".join(unit["source"])[:60]
                check_names = ", ".join(name for name, _desc in checks) or "?"
                detail = f"{i}/{len(units)}: {source_preview!r} — {check_names}"
            progress.update(i, detail)
            time.sleep(delay)
            entries.append((unit, checks))
        progress.finish()
        total += len(entries)

        cache_languages[lang] = [
            {"unit": unit, "checks": [list(c) for c in checks]}
            for unit, checks in entries
        ]
        body.extend(_render_language_section(lang, entries))

    header = [
        "# Weblate translation check report",
        "",
        f"Languages checked: {', '.join(languages)}  ",
        f"Total flagged strings: {total}",
        "",
    ]
    report = "\n".join(header + body)
    cache = {"project": project, "component": component, "languages": cache_languages}
    return report, cache


def _current_source_strings() -> set[str]:
    """Current set of extractable English source strings ("msgid", or
    "msgid / msgid_plural" for plurals — matching the API's array-joined
    format), via a fresh pybabel extract to a temp .pot. Mirrors
    scripts/check_translations.py. English has no .po file of its own (it's
    the source language), so this is how --recheck tells whether a flagged
    English string still exists in the codebase at all.
    """
    pybabel = str(REPO_ROOT / ".venv" / "bin" / "pybabel")
    babel_cfg = str(REPO_ROOT / "babel.cfg")
    with tempfile.NamedTemporaryFile(suffix=".pot", delete=False) as f:
        pot_path = Path(f.name)
    try:
        subprocess.run(
            [
                pybabel,
                "extract",
                "--no-wrap",
                "-F",
                babel_cfg,
                "-k",
                "_l",
                "-o",
                str(pot_path),
                str(REPO_ROOT),
            ],
            check=True,
            capture_output=True,
        )
        pot = polib.pofile(str(pot_path))
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReportError(f"Failed to extract current source strings: {exc}") from exc
    finally:
        pot_path.unlink(missing_ok=True)
    return {f"{e.msgid} / {e.msgid_plural}" if e.msgid_plural else e.msgid for e in pot}


def _current_po_map(lang: str) -> dict[str, str] | None:
    """Current {source: target} for a translated locale's committed .po file, in
    the same "a / b" join convention as the Weblate API's source/target arrays.
    None if the .po file doesn't exist for this language."""
    po_path = REPO_ROOT / "app" / "translations" / lang / "LC_MESSAGES" / "messages.po"
    if not po_path.is_file():
        return None
    po = polib.pofile(str(po_path))
    result: dict[str, str] = {}
    for e in po:
        if e.msgid_plural:
            key = f"{e.msgid} / {e.msgid_plural}"
            value = " / ".join(e.msgstr_plural[i] for i in sorted(e.msgstr_plural))
        else:
            key = e.msgid
            value = e.msgstr
        result[key] = value
    return result


def recheck_report(cache_path: Path, languages: list[str] | None, verbose: bool) -> str:
    if not cache_path.is_file():
        raise ReportError(
            f"No cached data at {cache_path} — run the script without --recheck "
            "first to fetch from Weblate."
        )
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    cached_languages: dict[str, list[dict[str, Any]]] = cache["languages"]
    langs = languages if languages else list(cached_languages)

    total = 0
    dropped_total = 0
    body: list[str] = []
    current_cache: dict[str, Any] = {}

    for lang in langs:
        cached_entries = cached_languages.get(lang, [])
        if lang not in current_cache:
            current_cache[lang] = (
                _current_source_strings() if lang == "en" else _current_po_map(lang)
            )
        current = current_cache[lang]

        kept: list[Entry] = []
        dropped = 0
        for item in cached_entries:
            unit = item["unit"]
            checks = [(c[0], c[1]) for c in item["checks"]]
            source = " / ".join(unit["source"])
            target = " / ".join(unit["target"])
            if lang == "en":
                still_flagged = current is not None and source in current
            else:
                still_flagged = current is not None and current.get(source) == target
            if still_flagged:
                kept.append((unit, checks))
            else:
                dropped += 1

        dropped_total += dropped
        total += len(kept)
        if verbose or dropped:
            _log(
                f"[{lang}] {dropped} of {len(cached_entries)} no longer match "
                "local files (fixed locally, not yet synced to Weblate)"
            )
        body.extend(_render_language_section(lang, kept))

    plural = "y" if dropped_total == 1 else "ies"
    _log(
        f"Rechecked {len(langs)} language(s) against local files — dropped "
        f"{dropped_total} entr{plural} already fixed locally."
    )

    header = [
        "# Weblate translation check report (rechecked against local files, not Weblate)",
        "",
        f"Languages checked: {', '.join(langs)}  ",
        f"Total flagged strings: {total}",
        "",
    ]
    return "\n".join(header + body)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "weblate_checks_report.md"),
        help="Path to write the Markdown report to (default: weblate_checks_report.md at the repo root)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("WEBLATE_API_TOKEN") or _dotenv_token(),
        help="Weblate API token (default: $WEBLATE_API_TOKEN, or a .env file at the repo root)",
    )
    parser.add_argument(
        "--languages",
        default=None,
        help="Comma-separated locale codes (default: all of SUPPORTED_LOCALES, including English)",
    )
    parser.add_argument(
        "--project",
        default="openhangar",
        help="Weblate project slug (default: %(default)s)",
    )
    parser.add_argument(
        "--component",
        default="openhangar",
        help="Weblate component slug (default: %(default)s)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.3,
        help="Seconds to sleep between per-string page fetches (default: %(default)s)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Also log each string's source text and detected check name(s) as they're fetched",
    )
    parser.add_argument(
        "--recheck",
        action="store_true",
        help=(
            "Don't query Weblate — re-derive the report from the last cached run "
            "(<output>.json) against the current local .po/.pot files, dropping "
            "entries already fixed locally but not yet synced to Weblate"
        ),
    )
    args = parser.parse_args()

    requested_languages = args.languages.split(",") if args.languages else None
    output_path = Path(args.output)
    cache_path = output_path.with_suffix(".json")

    try:
        if args.recheck:
            report = recheck_report(cache_path, requested_languages, args.verbose)
        else:
            if args.token:
                _log("Using Weblate API token (rate limit: 5000 requests/hour).")
            else:
                _log(
                    "No Weblate API token set — using anonymous access (rate limit: "
                    "100 requests/day). Fine for occasional runs; for frequent use, "
                    "create one at https://hosted.weblate.org/accounts/profile/#api "
                    "and set WEBLATE_API_TOKEN via --token, the environment "
                    "variable, or a WEBLATE_API_TOKEN=... line in a .env file at "
                    "the repo root."
                )
            report, cache = build_report(
                args.project,
                args.component,
                requested_languages or supported_locales(),
                args.token,
                args.delay,
                verbose=args.verbose,
            )
            cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except ReportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # Leading newline steps past a mid-redraw progress bar line (which has
        # no trailing newline yet on a real TTY) before the message.
        print("\nInterrupted — no report written.", file=sys.stderr)
        return 130

    output_path.write_text(report, encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
