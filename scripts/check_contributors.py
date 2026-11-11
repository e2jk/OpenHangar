#!/usr/bin/env python3
"""Keep the README's all-contributors list from going stale.

    check_contributors.py              # offline: README in sync with the list
    check_contributors.py --github     # also: every human contributor listed
    check_contributors.py --github --missing-as-warning   # (on pull requests)

Offline (pre-push hook and CI): every contributor in .all-contributorsrc has
their cell in the README table between the ALL-CONTRIBUTORS-LIST markers,
nobody else does, and the badge counts them all — i.e. the table was
regenerated after the list changed (or edited by hand to match).

--github (CI): every *person* who contributed through GitHub is listed — the
repository's commit authors and the authors of its merged pull requests,
plus, on a pull request, its author (PR_AUTHOR), so a first-time
contributor's own PR is where they get added. Bots (GitHub account type
"Bot": Renovate, Dependabot, GitHub Actions, AI coding agents) are never
expected. Needs GITHUB_REPOSITORY and GITHUB_TOKEN (or GH_TOKEN).

--missing-as-warning (CI on pull requests): someone missing is reported as
a GitHub warning annotation, not a failure — a first-time contributor's CI
run must never fail over this (nor anyone's over an earlier contributor not
yet added). On pushes it fails, which lands on the maintainer instead.

Contributions outside GitHub (e.g. translating on Weblate) can't be seen
here — those people are added by hand.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RC_FILE = ROOT / ".all-contributorsrc"
README = ROOT / "README.md"

# Other GitHub accounts of a listed contributor -> the login they're listed
# under (the one their commits are attributed to, so the table's links work).
ACCOUNT_ALIASES = {
    "kristofdvbe": "kristofdv",
}

_LIST_RE = re.compile(
    r"<!-- ALL-CONTRIBUTORS-LIST:START.*?-->(.*?)<!-- ALL-CONTRIBUTORS-LIST:END -->",
    re.DOTALL,
)
_BADGE_RE = re.compile(r"all_contributors-(\d+)-")
# Each cell links its contributions to commits?author=<login> or #<type>-<login>.
_CELL_RE = re.compile(r"<td\b.*?</td>", re.DOTALL)
_LOGIN_RE = re.compile(r'(?:commits\?author=|href="#[A-Za-z0-9]+-)([A-Za-z0-9-]+)"')


def listed_logins(rc: dict) -> list[str]:
    return [c["login"] for c in rc.get("contributors", [])]


def readme_problems(rc: dict, readme: str) -> list[str]:
    """What's out of sync between the list and the README."""
    problems: list[str] = []
    logins = listed_logins(rc)
    m = _LIST_RE.search(readme)
    if not m:
        return ["README.md has no ALL-CONTRIBUTORS-LIST section."]
    in_table: list[str] = []
    for cell in _CELL_RE.findall(m.group(1)):
        found = {x.lower() for x in _LOGIN_RE.findall(cell)}
        in_table.extend(sorted(found))
    wanted = {x.lower() for x in logins}
    for login in logins:
        if login.lower() not in in_table:
            problems.append(
                f"{login} is in .all-contributorsrc but not in the README table."
            )
    for login in in_table:
        if login not in wanted:
            problems.append(
                f"{login} is in the README table but not in .all-contributorsrc."
            )
    badge = _BADGE_RE.search(readme)
    if not badge:
        problems.append("README.md has no all-contributors badge.")
    elif int(badge.group(1)) != len(logins):
        problems.append(
            f"The README badge says {badge.group(1)} contributors; "
            f".all-contributorsrc lists {len(logins)}."
        )
    return problems


_API_HOST = "api.github.com"


def _get(path: str, token: str) -> list[dict]:
    """Every page of a GitHub API list endpoint (*path* on api.github.com —
    HTTPS to that host only, whatever a pagination link might say)."""
    items: list[dict] = []
    while path:
        conn = http.client.HTTPSConnection(_API_HOST, timeout=30)
        try:
            conn.request(
                "GET",
                path,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "openhangar-contributors-check",
                },
            )
            resp = conn.getresponse()
            body = resp.read()
            if resp.status != 200:
                raise RuntimeError(f"GitHub API {path}: HTTP {resp.status}")
            items.extend(json.loads(body))
            links = resp.getheader("Link", "") or ""
        finally:
            conn.close()
        nxt = re.search(r'<https://api\.github\.com(/[^>]+)>;\s*rel="next"', links)
        path = nxt.group(1) if nxt else ""
    return items


def github_humans(repo: str, token: str) -> set[str]:
    """Logins of the people (not bots) who authored commits or merged PRs."""
    api = f"/repos/{repo}"
    people: set[str] = set()
    for c in _get(f"{api}/contributors?per_page=100", token):
        if c.get("type") == "User":
            people.add(c["login"])
    for pr in _get(f"{api}/pulls?state=closed&per_page=100", token):
        user = pr.get("user") or {}
        if pr.get("merged_at") and user.get("type") == "User":
            people.add(user["login"])
    return people


def missing_people(rc: dict, people: set[str]) -> list[str]:
    """The *people* not listed (after resolving their other accounts)."""
    listed = {x.lower() for x in listed_logins(rc)}
    return sorted(
        p for p in people if ACCOUNT_ALIASES.get(p.lower(), p.lower()) not in listed
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github", action="store_true")
    parser.add_argument("--missing-as-warning", action="store_true")
    args = parser.parse_args(argv)
    rc = json.loads(RC_FILE.read_text(encoding="utf-8"))
    problems = readme_problems(rc, README.read_text(encoding="utf-8"))
    if args.github:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")
        if not repo or not token:
            print("--github needs GITHUB_REPOSITORY and GITHUB_TOKEN.", file=sys.stderr)
            return 2
        people = github_humans(repo, token)
        author = os.environ.get("PR_AUTHOR", "")
        if author and os.environ.get("PR_AUTHOR_TYPE", "User") == "User":
            people.add(author)
        for login in missing_people(rc, people):
            msg = (
                f"{login} contributed but isn't credited: add them to "
                ".all-contributorsrc and the README (see CONTRIBUTING.md, "
                '"Recognition").'
            )
            if args.missing_as_warning:
                print(f"::warning title=Contributors list::{msg}")
            else:
                problems.append(msg)
    for p in problems:
        print(f"[contributors] {p}", file=sys.stderr)
    if problems:
        return 1
    print("[contributors] OK — " + ", ".join(listed_logins(rc)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
