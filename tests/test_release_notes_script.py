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
        "For the next release: 1 feature, 2 fixes"
    )
    assert release_notes.title(["fix: one"], later=True) == "[next release] fix: one"
    assert release_notes.title([], later=True) == "ship-later"
    body = release_notes.body(["feat: x"], None, later=True)
    assert body.startswith("Auto-opened from `ship-later`")
    assert "without a release of its own" in body
    monkeypatch.setattr(sys, "stdin", io.StringIO("feat: a\nfix: b\n"))
    release_notes.main(["title", "--later"])
    assert capsys.readouterr().out == "For the next release: 1 feature, 1 fix\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO("feat: a\n"))
    release_notes.main(["body", "--later"])
    assert "ship-later" in capsys.readouterr().out
