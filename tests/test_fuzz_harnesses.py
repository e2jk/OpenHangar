"""Every fuzz harness still loads, and the fuzzing workflow runs each one.

The Fuzzing workflow only runs on main after a merge, so a harness importing
a helper that was removed (as the two offline-sync harnesses did when offline
editing went away) used to surface only there. atheris is not installed for
the test run; a stand-in provides the few names a harness uses at import.
"""

import contextlib
import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest  # pyright: ignore[reportMissingImports]

ROOT = Path(__file__).resolve().parent.parent
HARNESSES = sorted((ROOT / "fuzz").glob("fuzz_*.py"))


@pytest.fixture
def fake_atheris(monkeypatch):
    stub = types.ModuleType("atheris")
    stub.instrument_imports = lambda *a, **k: contextlib.nullcontext()  # type: ignore[attr-defined]
    stub.instrument_func = lambda f: f  # type: ignore[attr-defined]
    stub.FuzzedDataProvider = object  # type: ignore[attr-defined]
    stub.Setup = lambda *a, **k: None  # type: ignore[attr-defined]
    stub.Fuzz = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "atheris", stub)


@pytest.mark.parametrize("path", HARNESSES, ids=lambda p: p.stem)
def test_harness_imports(path, fake_atheris):
    spec = importlib.util.spec_from_file_location(f"_harness_{path.stem}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.TestOneInput)


def test_workflow_runs_exactly_the_harnesses_that_exist():
    workflow = (ROOT / ".github/workflows/fuzzing.yml").read_text()
    in_matrix = set(re.findall(r"^\s+- (fuzz_\w+)\s*$", workflow, re.MULTILINE))
    assert in_matrix == {p.stem for p in HARNESSES}
