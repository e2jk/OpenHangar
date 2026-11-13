"""The branch-coverage ratchet (scripts/check_branch_coverage.py, audit T01)."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "check_branch_coverage.py"

REPORT = """<?xml version="1.0" ?>
<coverage><packages><package><classes>
  <class filename="services/auth.py"><lines>
    <line number="10" hits="1" branch="true" condition-coverage="100% (2/2)"/>
    <line number="12" hits="1" branch="true" condition-coverage="50% (1/2)"/>
    <line number="14" hits="1"/>
  </lines></class>
  <class filename="services/plain.py"><lines><line number="1" hits="1"/></lines></class>
</classes></package></packages></coverage>
"""


@pytest.fixture
def ratchet(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("check_branch_coverage", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    baseline = tmp_path / "baseline.json"
    monkeypatch.setattr(module, "BASELINE", baseline)
    report = tmp_path / "report.xml"
    report.write_text(REPORT)
    return module, baseline, report


def test_passes_at_or_above_the_baseline(ratchet, capsys):
    module, baseline, report = ratchet
    baseline.write_text(
        json.dumps({"services/auth.py": 75.0, "services/plain.py": 100.0})
    )
    assert module.main([str(report)]) == 0
    assert "OK   services/auth.py: 75.0% of 4 branches" in capsys.readouterr().out


def test_fails_below_the_baseline_and_lists_the_missing_arcs(ratchet, capsys):
    module, baseline, report = ratchet
    baseline.write_text(json.dumps({"services/auth.py": 80.0}))
    assert module.main([str(report)]) == 1
    captured = capsys.readouterr()
    assert "DROP services/auth.py" in captured.out
    assert "line 12: 50% (1/2)" in captured.err


def test_a_module_missing_from_the_report_fails(ratchet):
    module, baseline, report = ratchet
    baseline.write_text(json.dumps({"services/gone.py": 50.0}))
    assert module.main([str(report)]) == 1


def test_update_only_ever_raises(ratchet):
    module, baseline, report = ratchet
    baseline.write_text(
        json.dumps({"services/auth.py": 10.0, "services/plain.py": 100.0})
    )
    assert module.main([str(report), "--update"]) == 0
    assert json.loads(baseline.read_text()) == {
        "services/auth.py": 75.0,
        "services/plain.py": 100.0,
    }
