"""scripts/ship.sh from a parallel worktree ships in its own lane.

Runs the real script against a throwaway bare remote: no GitHub access is
needed (a stub `gh` fails, which the script already treats as offline).
"""

import os
import shutil
import subprocess  # nosec B404 - runs git and the repo's own script in a temp dir
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="needs git and bash",
)


def _run(*args: str, cwd: Path, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(  # nosec B603 - fixed argument lists, temp paths
        list(args), cwd=cwd, env=env, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return result.stdout + result.stderr


@pytest.fixture
def repo(tmp_path: Path) -> dict[str, Path]:
    remote = tmp_path / "remote.git"
    main = tmp_path / "main"
    _run("git", "init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    _run("git", "init", "-b", "main", str(main), cwd=tmp_path)
    for key, value in (("user.name", "Test"), ("user.email", "test@example.test")):
        _run("git", "config", key, value, cwd=main)
    (main / "scripts").mkdir()
    for name in ("ship.sh", "release_notes.py"):
        shutil.copy(ROOT / "scripts" / name, main / "scripts" / name)
    (main / "README").write_text("start\n")
    _run("git", "add", ".", cwd=main)
    _run("git", "commit", "-m", "chore: start", cwd=main)
    _run("git", "remote", "add", "origin", str(remote), cwd=main)
    _run("git", "push", "origin", "main", cwd=main)
    lane = tmp_path / "Phase42 Work"
    _run("git", "worktree", "add", "-b", "Phase42_Places", str(lane), cwd=main)
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "gh").write_text("#!/bin/sh\nexit 1\n")
    (stub / "gh").chmod(0o755)
    return {"remote": remote, "main": main, "lane": lane, "bin": stub}


def _env(repo: dict[str, Path]) -> dict[str, str]:
    return {**os.environ, "PATH": f"{repo['bin']}:{os.environ['PATH']}"}


def _commit(path: Path, name: str) -> None:
    (path / name).write_text(name + "\n")
    _run("git", "add", name, cwd=path)
    _run("git", "commit", "-m", f"feat: add {name}", cwd=path)


def _remote_branches(repo: dict[str, Path]) -> set[str]:
    out = _run("git", "branch", "--format=%(refname:short)", cwd=repo["remote"])
    return set(out.split())


def test_linked_worktree_ships_later_in_its_own_lane(
    repo: dict[str, Path],
) -> None:
    _commit(repo["lane"], "places.txt")
    out = _run("bash", "scripts/ship.sh", "--later", cwd=repo["lane"], env=_env(repo))
    assert "Pushed to ship-later--phase42-places (lane phase42-places)" in out
    assert "ship-later--phase42-places" in _remote_branches(repo)
    # The main worktree keeps using ship-later, untouched by the lane.
    _commit(repo["main"], "audit.txt")
    _run("bash", "scripts/ship.sh", "--later", cwd=repo["main"], env=_env(repo))
    assert {"ship-later", "ship-later--phase42-places"} <= _remote_branches(repo)


def test_lane_refuses_an_immediate_release(repo: dict[str, Path]) -> None:
    _commit(repo["lane"], "places.txt")
    result = subprocess.run(  # nosec B603 - fixed argument list
        ["bash", "scripts/ship.sh"],
        cwd=repo["lane"],
        env=_env(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "only ships --later" in result.stderr
    assert "ship" not in _remote_branches(repo)


def test_explicit_lane_and_invalid_names(repo: dict[str, Path]) -> None:
    _commit(repo["main"], "docs.txt")
    out = _run(
        "bash",
        "scripts/ship.sh",
        "--later",
        "--lane",
        "Docs Pass",
        cwd=repo["main"],
        env=_env(repo),
    )
    assert "ship-later--docs-pass" in out
    for bad in ("---", "x" * 41):
        result = subprocess.run(  # nosec B603 - fixed argument list
            ["bash", "scripts/ship.sh", "--later", f"--lane={bad}"],
            cwd=repo["main"],
            env=_env(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1 and "Invalid lane name" in result.stderr
