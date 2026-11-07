#!/usr/bin/env python3
"""Build a ship PR's title and body from the batch's commit subjects.

Used by .github/workflows/auto-pr-merge.yml. Commit subjects (conventional
commits, one per line) come on stdin, oldest first.

    release_notes.py title [--later] < subjects
    release_notes.py body [--summary-file FILE] [--later] < subjects

The title counts the batch by kind ("Release: 7 features, 9 fixes, 1
performance") — or is the commit's own subject when there's only one. The
body is the optional hand-written summary (docs/release-notes/next.md, only
passed when it changed in this batch — see the workflow), then the commits
grouped by kind, with routine dependency updates collapsed into one line.

--later: the batch was shipped to `ship-later` (scripts/ship.sh --later) —
it merges without a release of its own and goes out with the next daily
release, so its title starts with "[later]" instead of "Release:".

The summary may start with a title line, `# <title>` (at most
MAX_TITLE characters — scripts/ship.sh checks before pushing): it is then
the PR's title instead of the count of commits by kind, which moves into
the body, and it isn't repeated in the body's summary. ci.yml names the
GitHub release after it the same way (`release_notes.py heading FILE`).

    release_notes.py heading FILE [--check]  # the title line, or nothing
    release_notes.py summary FILE            # the summary without it

--check (scripts/ship.sh): exit 1 when the title line is longer than
MAX_TITLE characters.
"""

from __future__ import annotations

import argparse
import re
import sys

# (conventional-commit type, section heading, word for the title count)
_KINDS = [
    ("feat", "Features", ("feature", "features")),
    ("fix", "Fixes", ("fix", "fixes")),
    ("perf", "Performance", ("performance", "performance")),
    ("docs", "Documentation", ("docs", "docs")),
    ("refactor", "Refactoring", ("refactor", "refactors")),
    ("test", "Tests", ("test", "tests")),
]
_OTHER = ("Other changes", ("other", "other"))
_TYPE_RE = re.compile(r"^(\w+)(\([^)]*\))?!?:\s*")
_DEPS_RE = re.compile(r"^(chore|fix|build)\(deps\)")


def _kind(subject: str) -> str:
    m = _TYPE_RE.match(subject)
    return m.group(1) if m else ""


def group(subjects: list[str]) -> tuple[dict[str, list[str]], int]:
    """Subjects per section heading, plus the number of dependency updates."""
    groups: dict[str, list[str]] = {}
    deps = 0
    for subject in subjects:
        if _DEPS_RE.match(subject):
            deps += 1
            continue
        kind = _kind(subject)
        heading = next((h for k, h, _w in _KINDS if k == kind), _OTHER[0])
        groups.setdefault(heading, []).append(subject)
    return groups, deps


# A PR title or release name longer than this is cut off in GitHub's lists
# and notifications (and it's the usual limit for a commit subject).
MAX_TITLE = 72
# Prefix of a ship-later PR's title: merges now, released with the next one.
_LATER = "[later]"


def split_summary(text: str | None) -> tuple[str | None, str]:
    """(title line or None, the rest) of a summary that may start with a
    `# <title>` line."""
    lines = (text or "").strip().splitlines()
    if lines and lines[0].startswith("# ") and lines[0][2:].strip():
        return lines[0][2:].strip(), "\n".join(lines[1:]).strip()
    return None, (text or "").strip()


def counts(subjects: list[str]) -> str:
    """The batch counted by kind: "7 features, 9 fixes, 2 dependency updates"."""
    groups, deps = group(subjects)
    parts = []
    for _k, heading, (one, many) in [*_KINDS, (None, *_OTHER)]:
        n = len(groups.get(heading, []))
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    if deps:
        parts.append(f"{deps} dependency update{'' if deps == 1 else 's'}")
    return ", ".join(parts)


def title(subjects: list[str], later: bool = False, summary: str | None = None) -> str:
    heading, _rest = split_summary(summary)
    if heading:
        # Hand-written; capped in case it slipped past ship.sh's check.
        if len(heading) > MAX_TITLE:
            heading = heading[: MAX_TITLE - 1].rstrip() + "…"
        return f"{_LATER} {heading}" if later else heading
    if not subjects:
        return "ship-later" if later else "ship"
    if len(subjects) == 1:
        return f"{_LATER} {subjects[0]}" if later else subjects[0]
    return f"{_LATER} {counts(subjects)}" if later else f"Release: {counts(subjects)}"


def body(subjects: list[str], summary: str | None, later: bool = False) -> str:
    if later:
        intro = (
            "Auto-opened from `ship-later` — will merge once CI passes, "
            "without a release of its own: it goes out with the next daily "
            "release (daily-release.yml, 23:20 UTC), or with the next "
            "`ship` before that."
        )
    else:
        intro = "Auto-opened from `ship` — will merge once CI passes."
    out = [intro, ""]
    if subjects:
        n = len(subjects)
        out += [f"{n} commit{'' if n == 1 else 's'}: {counts(subjects)}.", ""]
    _heading, rest = split_summary(summary)
    if rest:
        out += ["## Summary", "", rest, ""]
    groups, deps = group(subjects)
    for _k, heading, _w in [*_KINDS, (None, *_OTHER)]:
        if heading in groups:
            out += [f"## {heading}", ""] + [f"- {s}" for s in groups[heading]] + [""]
    if deps:
        out += ["## Dependencies", "", f"- {deps} routine dependency update(s)", ""]
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["title", "body", "heading", "summary"])
    parser.add_argument("file", nargs="?", help="heading/summary: the summary file")
    parser.add_argument("--summary-file")
    parser.add_argument("--later", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    path = args.file if args.mode in ("heading", "summary") else args.summary_file
    summary = None
    if path:
        with open(path, encoding="utf-8") as fh:
            summary = fh.read()
    if args.mode in ("heading", "summary"):
        heading, rest = split_summary(summary)
        if args.check and heading and len(heading) > MAX_TITLE:
            print(
                f"The title line of {path} is {len(heading)} characters long — "
                f"keep it to {MAX_TITLE} or fewer:\n  {heading}",
                file=sys.stderr,
            )
            return 1
        out = (heading or "") if args.mode == "heading" else rest
        if out:
            print(out)
        return 0
    subjects = [line.strip() for line in sys.stdin if line.strip()]
    if args.mode == "title":
        print(title(subjects, args.later, summary))
    else:
        sys.stdout.write(body(subjects, summary, args.later))
    return 0


if __name__ == "__main__":
    sys.exit(main())
