"""Replay each fuzz harness's persisted corpus to measure real line coverage.

Not a fuzzing run — no mutation, no time budget. Executes every input already
saved in fuzz/corpus/<harness>/ once through that harness's TestOneInput, so
CI can report how much of the fuzzed application code the corpus accumulated
so far (via fuzzing.yml's push-to-main and weekly runs) actually reaches.
Produces htmlcov-fuzz/ and coverage-fuzz.xml, mirroring the pytest coverage
report's htmlcov/ and coverage.xml so both can sit side by side on the
GitHub Pages site.
"""

from __future__ import annotations

import contextlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import atheris

# Disable Atheris's own coverage instrumentation for this replay — it exists
# to guide *mutation* during a live fuzzing run, and its bytecode rewriting
# has no bearing on which app code a corpus reaches. Leaving it enabled would
# also risk interfering with coverage.py's own line tracing. *args/**kwargs
# because harnesses call instrument_imports(include=[...]) to scope
# instrumentation to just their target module (see docs/development.md's
# "Fuzzing" section) — this stub must accept and ignore that too.
atheris.instrument_imports = lambda *args, **kwargs: contextlib.nullcontext()
atheris.instrument_func = lambda func: func

import coverage  # noqa: E402 — must follow the atheris monkeypatch above

_ROOT = Path(__file__).resolve().parent.parent
_FUZZ_DIR = _ROOT / "fuzz"

# The specific modules fuzz harnesses import a real function from — not a
# broad app/* glob. A broad glob also picks up whatever those modules
# transitively import at module level (e.g. models.py, utils.py), which
# "cover" highly just from class/def bodies executing at import time, not
# from anything a harness actually exercises. Update this list whenever a
# new harness starts importing from a module not already covered here.
_TARGET_MODULES = [
    "app/reservations/routes.py",  # fuzz_safe_next
    "app/documents/routes.py",  # fuzz_safe_join, fuzz_safe_path_component
    "app/pilots/logbook_import.py",  # fuzz_logbook_parse_file, fuzz_logbook_value_parsers
    "app/aircraft/gps_import.py",  # fuzz_gps_import
    "app/flights/form_parsing.py",  # fuzz_flight_form_parsing
    "app/pilots/form_parsing.py",  # fuzz_pilot_form_parsing
    "app/maintenance/form_parsing.py",  # fuzz_maintenance_form_parsing
]


def _load_harness(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    harness_paths = sorted(_FUZZ_DIR.glob("fuzz_*.py"))
    cov = coverage.Coverage(
        config_file=False,
        data_file=str(_ROOT / ".coverage.fuzz"),
        include=[str(_ROOT / m) for m in _TARGET_MODULES],
    )
    cov.start()

    replayed = 0
    for path in harness_paths:
        corpus_dir = _FUZZ_DIR / "corpus" / path.stem
        if not corpus_dir.is_dir():
            print(f"[fuzz-coverage] {path.stem}: no corpus, skipping")
            continue
        module = _load_harness(path)
        files = sorted(f for f in corpus_dir.iterdir() if f.is_file())
        for f in files:
            try:
                module.TestOneInput(f.read_bytes())
            except Exception as exc:
                # Replaying a corpus of previously-accepted, non-crashing
                # inputs — a replay-time exception just means the crash file
                # was never pruned from the corpus dir; don't let it abort
                # the whole report.
                print(f"[fuzz-coverage] {path.stem}: {f.name} raised {exc!r} on replay")
        print(f"[fuzz-coverage] {path.stem}: replayed {len(files)} corpus file(s)")
        replayed += len(files)

    cov.stop()
    cov.save()

    if replayed == 0:
        print("[fuzz-coverage] no corpus files found anywhere — nothing to report")
        return 0

    cov.html_report(directory=str(_ROOT / "htmlcov-fuzz"))
    cov.xml_report(outfile=str(_ROOT / "coverage-fuzz.xml"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
