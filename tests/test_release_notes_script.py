"""Tests for scripts/release_notes.py (ship PR title and body)."""

import importlib.util
import io
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "release_notes.py"
_spec = importlib.util.spec_from_file_location("release_notes", _PATH)
release_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_notes)

SUBJECTS = [
    "feat(fuel): add a fuel log",
    "fix(import): keep the scroll position",
    "fix(ui): navbar labels on one line",
    "perf(import): cache Excel parses",
    "chore(deps): update dependency ruff to v0.17.2",
    "chore(deps): update dependency certifi",
    "docs(agent-install): detect 32-bit userland",
    "ci: tweak workflow",
]


def test_title_counts_by_kind():
    assert release_notes.title(SUBJECTS) == (
        "Release: 1 feature, 2 fixes, 1 performance, 1 docs, 1 other, "
        "2 dependency updates"
    )


def test_title_single_commit_and_empty():
    assert release_notes.title(["fix: one thing"]) == "fix: one thing"
    assert release_notes.title([]) == "ship"


def test_body_groups_and_summary():
    body = release_notes.body(SUBJECTS, "Fuel tracking!\n")
    assert body.index("## Summary") < body.index("## Features") < body.index("## Fixes")
    assert "Fuel tracking!" in body
    assert "- fix(ui): navbar labels on one line" in body
    assert "## Other changes\n\n- ci: tweak workflow" in body
    assert "- 2 routine dependency update(s)" in body
    assert "ruff" not in body


def test_body_without_summary():
    body = release_notes.body(["feat: x"], "   \n")
    assert "## Summary" not in body and "## Features\n\n- feat: x" in body


def test_cli(monkeypatch, capsys, tmp_path):
    summary = tmp_path / "next.md"
    summary.write_text("Hello")
    monkeypatch.setattr(sys, "stdin", io.StringIO("feat: a\nfix: b\n\n"))
    release_notes.main(["body", "--summary-file", str(summary)])
    assert "Hello" in capsys.readouterr().out
    monkeypatch.setattr(sys, "stdin", io.StringIO("feat: a\nfix: b\n"))
    release_notes.main(["title"])
    assert capsys.readouterr().out == "Release: 1 feature, 1 fix\n"


def test_later_title_and_body(monkeypatch, capsys):
    assert release_notes.title(SUBJECTS, later=True).startswith(
        "[later] 1 feature, 2 fixes"
    )
    assert release_notes.title(["fix: one"], later=True) == "[later] fix: one"
    assert release_notes.title([], later=True) == "ship-later"
    body = release_notes.body(["feat: x"], None, later=True)
    assert body.startswith("Auto-opened from `ship-later`")
    assert "without a release of its own" in body
    monkeypatch.setattr(sys, "stdin", io.StringIO("feat: a\nfix: b\n"))
    release_notes.main(["title", "--later"])
    assert capsys.readouterr().out == "[later] 1 feature, 1 fix\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO("feat: a\n"))
    release_notes.main(["body", "--later"])
    assert "ship-later" in capsys.readouterr().out


SUMMARY = (
    "# GPS import: several files per flight, ground runs\n\n**GPS**\n- Ground runs.\n"
)


def test_title_from_the_summary_title_line():
    assert release_notes.title(SUBJECTS, summary=SUMMARY) == (
        "GPS import: several files per flight, ground runs"
    )
    assert release_notes.title(SUBJECTS, later=True, summary=SUMMARY) == (
        "[later] GPS import: several files per flight, ground runs"
    )
    # No title line, or an empty one: the counts, as before.
    assert release_notes.title(SUBJECTS, summary="**GPS**\n- x").startswith("Release: ")
    assert release_notes.title(SUBJECTS, summary="# \n- x").startswith("Release: ")
    # Too long (ship.sh refuses it; capped anyway).
    long = release_notes.title(["feat: x"], summary="# " + "word " * 30)
    assert len(long) == release_notes.MAX_TITLE and long.endswith("…")


def test_body_with_title_line_and_counts():
    body = release_notes.body(SUBJECTS, SUMMARY)
    assert "# GPS import" not in body
    assert "## Summary\n\n**GPS**\n- Ground runs." in body
    assert "8 commits: 1 feature, 2 fixes," in body
    assert "1 commit: 1 feature." in release_notes.body(["feat: x"], None)
    # Only a title line: no summary section.
    assert "## Summary" not in release_notes.body(["feat: x"], "# Just a title\n")


def test_heading_and_summary_cli(capsys, tmp_path):
    f = tmp_path / "next.md"
    f.write_text(SUMMARY)
    release_notes.main(["heading", str(f)])
    assert (
        capsys.readouterr().out == "GPS import: several files per flight, ground runs\n"
    )
    release_notes.main(["summary", str(f)])
    assert capsys.readouterr().out == "**GPS**\n- Ground runs.\n"
    f.write_text("- no title\n")
    release_notes.main(["heading", str(f)])
    assert capsys.readouterr().out == ""
    f.write_text("")
    release_notes.main(["summary", str(f)])
    assert capsys.readouterr().out == ""


def test_heading_check(capsys, tmp_path):
    f = tmp_path / "next.md"
    f.write_text("# " + "x" * 73 + "\n")
    assert release_notes.main(["heading", str(f), "--check"]) == 1
    assert "73 characters long" in capsys.readouterr().err
    f.write_text("# " + "x" * 72 + "\n")
    assert release_notes.main(["heading", str(f), "--check"]) == 0


NOTES = """## What's Changed
* GPX tracks from cameras by @e2jk in https://github.com/o/r/pull/434
* [later] refactor: address CodeQL notes by @e2jk in https://github.com/o/r/pull/435
  * refactor: one commit
  * docs: another

**Full Changelog**: https://github.com/o/r/compare/v1...v2
"""


def test_drop_released_prs():
    out = release_notes.drop_released(NOTES, {"https://github.com/o/r/pull/435"})
    assert "pull/434" in out and "pull/435" not in out and "one commit" not in out
    assert out.endswith("compare/v1...v2\n")
    # Nothing new left: no empty "What's Changed" heading.
    both = {"https://github.com/o/r/pull/434", "https://github.com/o/r/pull/435"}
    out = release_notes.drop_released("## Summary\n\nx\n\n" + NOTES, both)
    assert "What's Changed" not in out and out.startswith("## Summary")
    assert "**Full Changelog**" in out
    assert release_notes.drop_released(NOTES, set()) == NOTES


def test_fallback_title():
    one = NOTES.replace(
        "* GPX tracks from cameras by @e2jk in https://github.com/o/r/pull/434\n", ""
    )
    assert release_notes.fallback_title(one) == "refactor: address CodeQL notes"
    assert release_notes.fallback_title(NOTES) == ""  # several: no guess
    deps = (
        "* chore(deps): update a by @renovate[bot] in u/1\n"
        "* fix(deps): update b [SECURITY] by @renovate[bot] in u/2\n"
    )
    assert release_notes.fallback_title(deps) == "Dependency updates"
    assert release_notes.fallback_title("no PR list") == ""
    long = "* feat: " + "word " * 30 + "by @e in u/3\n"
    capped = release_notes.fallback_title(long)
    assert len(capped) <= release_notes.MAX_TITLE and capped.endswith("…")


def test_drop_released_and_fallback_cli(capsys, tmp_path):
    notes = tmp_path / "notes.md"
    urls = tmp_path / "urls.txt"
    notes.write_text(NOTES)
    urls.write_text("https://github.com/o/r/pull/434\n\n")
    release_notes.main(["drop-released", str(notes), str(urls)])
    assert "pull/434" not in notes.read_text()
    release_notes.main(["fallback-title", str(notes)])
    assert capsys.readouterr().out == "refactor: address CodeQL notes\n"
    notes.write_text("nothing")
    release_notes.main(["fallback-title", str(notes)])
    assert capsys.readouterr().out == ""
