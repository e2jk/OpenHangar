#!/usr/bin/env python3
"""Build a ship PR's title and body from the batch's commit subjects.

Used by .github/workflows/auto-pr-merge.yml. Commit subjects (conventional
commits, one per line) come on stdin, oldest first.

    release_notes.py title < subjects
    release_notes.py body [--summary-file FILE] < subjects

The title counts the batch by kind ("Release: 7 features, 9 fixes, 1
performance") — or is the commit's own subject when there's only one. The
body is the optional hand-written summary (docs/release-notes/next.md, only
passed when it changed in this batch — see the workflow), then the commits
grouped by kind, with routine dependency updates collapsed into one line.
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


def title(subjects: list[str]) -> str:
    if not subjects:
        return "ship"
    if len(subjects) == 1:
        return subjects[0]
    groups, deps = group(subjects)
    parts = []
    for _k, heading, (one, many) in [*_KINDS, (None, *_OTHER)]:
        n = len(groups.get(heading, []))
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    if deps:
        parts.append(f"{deps} dependency update{'' if deps == 1 else 's'}")
    return "Release: " + ", ".join(parts)


def body(subjects: list[str], summary: str | None) -> str:
    out = ["Auto-opened from `ship` — will merge once CI passes.", ""]
    if summary and summary.strip():
        out += ["## Summary", "", summary.strip(), ""]
    groups, deps = group(subjects)
    for _k, heading, _w in [*_KINDS, (None, *_OTHER)]:
        if heading in groups:
            out += [f"## {heading}", ""] + [f"- {s}" for s in groups[heading]] + [""]
    if deps:
        out += ["## Dependencies", "", f"- {deps} routine dependency update(s)", ""]
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["title", "body"])
    parser.add_argument("--summary-file")
    args = parser.parse_args(argv)
    subjects = [line.strip() for line in sys.stdin if line.strip()]
    if args.mode == "title":
        print(title(subjects))
    else:
        summary = None
        if args.summary_file:
            with open(args.summary_file, encoding="utf-8") as fh:
                summary = fh.read()
        sys.stdout.write(body(subjects, summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
