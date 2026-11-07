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
