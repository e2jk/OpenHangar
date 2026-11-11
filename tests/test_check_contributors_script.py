"""Tests for scripts/check_contributors.py (all-contributors list kept current)."""

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_contributors.py"
_spec = importlib.util.spec_from_file_location("check_contributors", _PATH)
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

RC = {"contributors": [{"login": "alice"}, {"login": "Bob"}]}


def _readme(logins, badge=None):
    cells = "".join(
        f'<td><a href="https://github.com/o/r/commits?author={x}">💻</a> '
        f'<a href="#translation-{x}">🌍</a></td>'
        for x in logins
    )
    n = len(logins) if badge is None else badge
    return (
        f"[![All Contributors](https://img.shields.io/badge/all_contributors-{n}-orange.svg)]\n"
        "<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->\n"
        f"<table><tr>{cells}</tr></table>\n"
        "<!-- ALL-CONTRIBUTORS-LIST:END -->\n"
    )


def test_readme_in_sync():
    assert cc.readme_problems(RC, _readme(["alice", "Bob"])) == []


def test_readme_out_of_sync():
    problems = cc.readme_problems(RC, _readme(["alice", "carol"], badge=3))
    assert problems == [
        "Bob is in .all-contributorsrc but not in the README table.",
        "carol is in the README table but not in .all-contributorsrc.",
        "The README badge says 3 contributors; .all-contributorsrc lists 2.",
    ]
    assert cc.readme_problems(RC, "no list") == [
        "README.md has no ALL-CONTRIBUTORS-LIST section."
    ]
    no_badge = _readme(["alice", "Bob"]).split("\n", 1)[1]
    assert cc.readme_problems(RC, no_badge) == [
        "README.md has no all-contributors badge."
    ]


def test_missing_people_resolves_other_accounts(monkeypatch):
    monkeypatch.setattr(cc, "ACCOUNT_ALIASES", {"bob-work": "bob"})
    assert cc.missing_people(RC, {"ALICE", "bob-work", "dave"}) == ["dave"]


def test_the_real_readme_matches_the_real_list():
    rc = json.loads(cc.RC_FILE.read_text(encoding="utf-8"))
    assert cc.readme_problems(rc, cc.README.read_text(encoding="utf-8")) == []


class _Resp:
    def __init__(self, status, body, link=""):
        self.status, self._body, self._link = status, body, link

    def read(self):
        return json.dumps(self._body).encode()

    def getheader(self, name, default=None):
        return self._link or default


def _fake_api(pages):
    """HTTPSConnection stand-in serving *pages* ({path: _Resp})."""
    seen = []

    class Conn:
        def __init__(self, host, timeout):
            assert host == "api.github.com"

        def request(self, method, path, headers):
            seen.append(path)
            self.path = path

        def getresponse(self):
            return pages[self.path]

        def close(self):
            pass

    return Conn, seen


def test_get_follows_pages_on_github_only(monkeypatch):
    conn, seen = _fake_api(
        {
            "/a": _Resp(200, [1], '<https://api.github.com/a?page=2>; rel="next"'),
            "/a?page=2": _Resp(200, [2], '<https://evil.example/x>; rel="next"'),
            "/b": _Resp(404, {}),
        }
    )
    monkeypatch.setattr(cc.http.client, "HTTPSConnection", conn)
    # A "next" link to another host is not followed.
    assert cc._get("/a", "t") == [1, 2]
    assert seen == ["/a", "/a?page=2"]
    with pytest.raises(RuntimeError, match="HTTP 404"):
        cc._get("/b", "t")


def test_github_humans_skips_bots_and_unmerged(monkeypatch):
    conn, _seen = _fake_api(
        {
            "/repos/o/r/contributors?per_page=100": _Resp(
                200,
                [
                    {"login": "alice", "type": "User"},
                    {"login": "renovate[bot]", "type": "Bot"},
                ],
            ),
            "/repos/o/r/pulls?state=closed&per_page=100": _Resp(
                200,
                [
                    {"merged_at": "x", "user": {"login": "carol", "type": "User"}},
                    {"merged_at": None, "user": {"login": "eve", "type": "User"}},
                    {"merged_at": "x", "user": {"login": "Copilot", "type": "Bot"}},
                    {"merged_at": "x", "user": None},
                ],
            ),
        }
    )
    monkeypatch.setattr(cc.http.client, "HTTPSConnection", conn)
    assert cc.github_humans("o/r", "t") == {"alice", "carol"}


def test_main(monkeypatch, capsys):
    for var in ("GITHUB_REPOSITORY", "GITHUB_TOKEN", "GH_TOKEN", "PR_AUTHOR"):
        monkeypatch.delenv(var, raising=False)
    assert cc.main([]) == 0
    assert "[contributors] OK" in capsys.readouterr().out
    assert cc.main(["--github"]) == 2

    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(cc, "github_humans", lambda repo, token: {"e2jk"})
    assert cc.main(["--github"]) == 0
    # A first-time contributor's own pull request: they must be added…
    monkeypatch.setenv("PR_AUTHOR", "newcomer")
    assert cc.main(["--github"]) == 1
    assert "newcomer contributed but isn't credited" in capsys.readouterr().err
    # …but on a pull request that's a warning, not a failure.
    assert cc.main(["--github", "--missing-as-warning"]) == 0
    out = capsys.readouterr().out
    assert "::warning title=Contributors list::newcomer contributed" in out
    assert "[contributors] OK" in out
    # A bot's pull request: nobody to add.
    monkeypatch.setenv("PR_AUTHOR_TYPE", "Bot")
    assert cc.main(["--github"]) == 0
