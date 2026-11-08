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
    assert release_notes.fallback_title(NOTES) == "GPX tracks from cameras (+1 more)"
    deps = (
        "* chore(deps): update a by @renovate[bot] in u/1\n"
        "* fix(deps): update b [SECURITY] by @renovate[bot] in u/2\n"
    )
    assert release_notes.fallback_title(deps) == "Dependency updates"
    assert release_notes.fallback_title("no PR list") == ""
    long = "* feat: " + "word " * 30 + "by @e in u/3\n"
    capped = release_notes.fallback_title(long)
    assert len(capped) <= release_notes.MAX_TITLE and capped.endswith("…")


def test_release_name(capsys, tmp_path):
    one = NOTES.replace(
        "* GPX tracks from cameras by @e2jk in https://github.com/o/r/pull/434\n", ""
    )
    assert release_notes.release_name("3.37.0", "", one) == (
        "v3.37.0 — refactor: address CodeQL notes"
    )
    assert release_notes.release_name("3.38.0", "SkyDemon exports", NOTES) == (
        "v3.38.0 — SkyDemon exports"
    )
    assert release_notes.release_name("3.38.0", None, NOTES) == (
        "v3.38.0 — GPX tracks from cameras (+1 more)"
    )
    assert release_notes.release_name("3.38.0", None, "no PRs") == "v3.38.0"
    security = (
        "* chore(deps): update security patches [security] [security]"
        " by @renovate[bot] in u/9\n"
    )
    assert release_notes.release_name("3.38.1", "", security) == (
        "v3.38.1 — Dependency updates [SECURITY]"
    )
    assert release_notes.release_name(
        "3.39.0", "SkyDemon exports", NOTES + security
    ) == ("v3.39.0 — SkyDemon exports [SECURITY]")
    # Several PRs, no title line, one of them a security fix.
    assert release_notes.release_name("3.39.0", "", NOTES + security) == (
        "v3.39.0 — GPX tracks from cameras (+2 more) [SECURITY]"
    )
    notes = tmp_path / "notes.md"
    notes.write_text(security)
    release_notes.main(["release-name", "3.38.1", str(notes), "--heading", ""])
    assert capsys.readouterr().out == "v3.38.1 — Dependency updates [SECURITY]\n"


def test_drop_released_cli(tmp_path):
    notes = tmp_path / "notes.md"
    urls = tmp_path / "urls.txt"
    notes.write_text(NOTES)
    urls.write_text("https://github.com/o/r/pull/434\n\n")
    release_notes.main(["drop-released", str(notes), str(urls)])
    assert "pull/434" not in notes.read_text()


def test_previous_release(monkeypatch, capsys):
    tags = ["v3.37.0", "v3.36.0", "v3.9.9", "v3.36.1", "draft", "v3.38.0", ""]
    assert release_notes.previous_release("3.37.0", tags) == "v3.36.1"
    assert release_notes.previous_release("3.36.0", tags) == "v3.9.9"  # numeric order
    assert release_notes.previous_release("0.1.0", tags) == ""
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(tags)))
    release_notes.main(["previous-release", "3.38.0"])
    assert capsys.readouterr().out == "v3.37.0\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO("v9.0.0\n"))
    release_notes.main(["previous-release", "1.0.0"])
    assert capsys.readouterr().out == ""


def test_name_from_titles():
    name = release_notes.name_from_titles
    assert name([]) == ""
    assert name(["[later] fix: one thing"]) == "fix: one thing"
    assert name(["chore(deps): a", "fix(deps): b"]) == "Dependency updates"
    # The first meaningful title, skipping dependency updates and generic
    # "Release: …" counts.
    assert (
        name(["chore(deps): a", "Release: 2 fixes", "feat: real"])
        == "feat: real (+2 more)"
    )
    assert name(["Release: 2 fixes", "chore(deps): a"]) == "Release: 2 fixes (+1 more)"
    long = name(["feat: " + "word " * 30, "fix: x"])
    assert len(long) <= release_notes.MAX_TITLE and long.endswith("… (+1 more)")


def test_versions_json(monkeypatch, capsys):
    tags = ["v3.37.0", "v3.9.9", "v3.36.1", "draft", "v0.5.0", ""]
    assert release_notes.versions_list("3.38.0", tags) == [
        "3.38.0",
        "3.37.0",
        "3.36.1",
        "3.9.9",
        "0.5.0",
    ]
    # The version being released already listed: once.
    assert release_notes.versions_list("3.37.0", ["v3.37.0"]) == ["3.37.0"]
    monkeypatch.setattr(sys, "stdin", io.StringIO("v1.0.0\nv1.0.1\n"))
    release_notes.main(["versions-json", "1.1.0"])
    assert capsys.readouterr().out == '["1.1.0", "1.0.1", "1.0.0"]\n'
