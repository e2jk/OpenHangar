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
    release_notes.py release-name VERSION NOTES [--heading TITLE]
    release_notes.py previous-release VERSION < tags  # the release before
    release_notes.py drop-released NOTES URLS
        # remove from GitHub-generated release NOTES (in place) the PRs
        # whose URLs are listed in the URLS file — see drop_released

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


def _pr_titles(notes: str) -> list[str]:
    """The PR titles of GitHub-generated release notes
    ("* <title> by @<author> in <url>")."""
    return [
        line[2 : line.rfind(" by @")]
        for line in notes.splitlines()
        if line.startswith("* ") and " by @" in line
    ]


_SECURITY_RE = re.compile(r"\s*\[security\]", re.IGNORECASE)


def name_from_titles(titles: list[str]) -> str:
    """A release name from the titles of what it contains (PRs, or commits):
    the only one's own title (without the "[later]" prefix); "Dependency
    updates" when they all are; otherwise the first meaningful one — not a
    dependency update, nor a generic "Release: …" count — with
    "(+N more)"; "" when there's nothing."""
    titles = [t[len(_LATER) + 1 :] if t.startswith(_LATER + " ") else t for t in titles]
    if not titles:
        return ""
    if all(_DEPS_RE.match(t) for t in titles):
        return "Dependency updates"
    if len(titles) == 1:
        main, more = titles[0], ""
    else:
        candidates = [t for t in titles if not _DEPS_RE.match(t)]
        main = next(
            (t for t in candidates if not t.startswith("Release: ")), candidates[0]
        )
        more = f" (+{len(titles) - 1} more)"
    room = MAX_TITLE - len(more)
    if len(main) > room:
        main = main[: room - 1].rstrip() + "…"
    return main + more


def fallback_title(notes: str) -> str:
    """A name for a release whose summary has no title line, from the PRs
    its release notes list (name_from_titles)."""
    return name_from_titles(_pr_titles(notes))


def release_name(version: str, heading: str | None, notes: str) -> str:
    """The GitHub release's name: "v<version> — <name>", the name being the
    summary's title line (*heading*) or else fallback_title(); tagged
    "[SECURITY]" when one of its PRs is a security fix (Renovate's
    vulnerability-alert PRs carry "[security]" in their title) — just
    "v<version>" when there's nothing to name it after."""
    name = (heading or "").strip() or fallback_title(notes)
    if any(_SECURITY_RE.search(t) for t in _pr_titles(notes)):
        name = f"{_SECURITY_RE.sub('', name).strip() or 'Security update'} [SECURITY]"
    return f"v{version} — {name}" if name else f"v{version}"


_VERSION_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def previous_release(version: str, tags: list[str]) -> str:
    """The highest "vX.Y.Z" tag in *tags* below *version* ("" if none)."""
    me = tuple(int(n) for n in version.split("."))
    older = [
        (key, tag)
        for tag in (t.strip() for t in tags)
        if (m := _VERSION_TAG_RE.match(tag))
        and (key := tuple(int(n) for n in m.groups())) < me
    ]
    return max(older)[1] if older else ""


def drop_released(notes: str, released_urls: set[str]) -> str:
    """GitHub-generated release notes without the PRs in *released_urls*
    (and their sub-bullets), dropping the "What's Changed" heading if no PR
    is left under it.

    Why: a release's notes list the PRs merged since the previous minor
    tag, and that tag sits on the commit its release was built from — a
    ship PR's head, published before the PR merges — so that PR counts as
    merged after the tag and shows up again in the next release's notes.
    ci.yml passes the PRs the previous release's notes already list."""
    out: list[str] = []
    skipping = False
    for line in notes.splitlines():
        if line.startswith("* "):
            skipping = any(
                line.rstrip().endswith(" in " + url) for url in released_urls
            )
        elif not line.startswith("  "):
            skipping = False
        if not skipping:
            out.append(line)
    text = "\n".join(out)
    head, sep, rest = text.partition("## What's Changed")
    if sep and not any(
        line.startswith("* ")
        for line in rest.split("**Full Changelog**")[0].splitlines()
    ):
        text = head.rstrip("\n") + ("\n\n" if head.strip() else "") + rest.lstrip("\n")
    return text.strip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=[
            "title",
            "body",
            "heading",
            "summary",
            "drop-released",
            "release-name",
            "previous-release",
        ],
    )
    parser.add_argument("file", nargs="?", help="heading/summary: the summary file")
    parser.add_argument(
        "urls", nargs="?", help="drop-released: file of PR URLs; release-name: notes"
    )
    parser.add_argument("--heading", default="")
    parser.add_argument("--summary-file")
    parser.add_argument("--later", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.mode == "previous-release":  # file = version, tags on stdin
        found = previous_release(args.file, sys.stdin.read().splitlines())
        if found:
            print(found)
        return 0
    if args.mode == "release-name":  # file = version, urls = the notes
        with open(args.urls, encoding="utf-8") as fh:
            print(release_name(args.file, args.heading, fh.read()))
        return 0
    if args.mode == "drop-released":
        with open(args.urls, encoding="utf-8") as fh:
            urls = {line.strip() for line in fh if line.strip()}
        with open(args.file, encoding="utf-8") as fh:
            notes = fh.read()
        with open(args.file, "w", encoding="utf-8") as fh:
            fh.write(drop_released(notes, urls))
        return 0
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
