"""Keep targeted validation separate from the authoritative coverage artifacts."""

import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run-tests-with-coverage.sh"


@pytest.fixture
def runner(tmp_path):
    executable = tmp_path / ".venv/bin/pytest"
    executable.parent.mkdir(parents=True)
    executable.write_text(
        "#!/usr/bin/env python3\nimport json, sys\n"
        'with open("calls.jsonl", "a") as stream:\n'
        '    stream.write(json.dumps(sys.argv[1:]) + "\\n")\n'
    )
    executable.chmod(0o755)
    (tmp_path / "coverage.xml").write_text("authoritative full-suite report")

    def run(*args):
        result = subprocess.run(
            ["bash", str(SCRIPT), *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        calls = tmp_path / "calls.jsonl"
        recorded = (
            [json.loads(line) for line in calls.read_text().splitlines()]
            if calls.exists()
            else []
        )
        assert (
            tmp_path / "coverage.xml"
        ).read_text() == "authoritative full-suite report"
        return result, recorded

    return run


def test_targeted_forwards_selectors_as_separate_arguments_without_coverage(runner):
    result, calls = runner(
        "--targeted",
        "tests/test_example.py::test_case",
        "-k",
        "one or two",
        "--durations",
    )
    assert result.returncode == 0
    assert calls == [
        [
            "tests/test_example.py::test_case",
            "-k",
            "one or two",
            "--durations=20",
            "--no-cov",
        ]
    ]
    assert "full 100% gate is still required" in result.stdout


@pytest.mark.parametrize("mode", [[], ["--durations"], ["--all"]])
def test_full_modes_keep_unscoped_coverage_gate(runner, mode):
    result, calls = runner(*mode)
    assert result.returncode == 0
    assert "--cov=app" in calls[0]
    assert "--cov-fail-under=100" in calls[0]
    assert "--cov-report=xml:coverage.xml" in calls[0]
    assert "--junitxml=test-results.xml" in calls[0]
    assert len(calls) == (2 if mode == ["--all"] else 1)
    if mode == ["--durations"]:
        assert "--durations=20" in calls[0]
    if mode == ["--all"]:
        assert calls[1] == ["--e2e", "tests/e2e/", "--override-ini=addopts="]


def test_e2e_mode_keeps_browser_suite_separate(runner):
    result, calls = runner("--e2e")
    assert result.returncode == 0
    assert calls == [["--e2e", "tests/e2e/", "--override-ini=addopts="]]


@pytest.mark.parametrize(
    "args",
    [
        ["tests/test_example.py"],
        ["--all", "-k", "auth"],
        ["--cov-fail-under=0"],
        ["--targeted"],
        ["--targeted", "tests/test_example.py", "--cov=app"],
        ["--targeted", "--junitxml=coverage.xml"],
    ],
)
def test_ambiguous_or_report_overriding_arguments_fail_before_pytest(runner, args):
    result, calls = runner(*args)
    assert result.returncode == 2
    assert calls == []
    assert result.stderr
