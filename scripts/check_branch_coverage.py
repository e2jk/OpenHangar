#!/usr/bin/env python3
"""Branch-coverage ratchet for security- and money-critical modules (audit T01).

Reads a Cobertura XML report written with branch measurement
(scripts/branch-coverage.sh) and compares each module listed in
scripts/branch-coverage-baseline.json with its recorded branch coverage.
Exits 1 if any falls below it, printing that module's missing branches.

--update raises (never lowers) the recorded baselines to the current
values, for after tests improved.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import defusedxml.ElementTree as ET  # pyright: ignore[reportMissingImports]

BASELINE = Path(__file__).with_name("branch-coverage-baseline.json")


def branch_rates(report: Path) -> dict[str, tuple[int, int, list[str]]]:
    """{app-relative path: (branches covered, branches valid, missing arcs)}."""
    rates: dict[str, tuple[int, int, list[str]]] = {}
    root = ET.parse(report).getroot()
    for cls in root.iter("class"):
        covered = valid = 0
        missing: list[str] = []
        for line in cls.iter("line"):
            if line.get("branch") != "true":
                continue
            cond = line.get("condition-coverage", "")  # e.g. "50% (1/2)"
            done, total = cond.split("(")[1].rstrip(")").split("/")
            covered += int(done)
            valid += int(total)
            if int(done) < int(total):
                missing.append(f"line {line.get('number')}: {cond}")
        rates[cls.get("filename", "")] = (covered, valid, missing)
    return rates


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args(argv)

    baseline: dict[str, float] = json.loads(BASELINE.read_text())
    rates = branch_rates(args.report)
    failed = False
    for module, recorded in sorted(baseline.items()):
        if module not in rates:
            print(f"MISSING  {module}: not in the report", file=sys.stderr)
            failed = True
            continue
        covered, valid, missing = rates[module]
        current = 100.0 if valid == 0 else round(100.0 * covered / valid, 1)
        if args.update and current > recorded:
            baseline[module] = current
        status = "OK  " if current >= recorded else "DROP"
        print(
            f"{status} {module}: {current}% of {valid} branches (baseline {recorded}%)"
        )
        if current < recorded:
            failed = True
            for arc in missing:
                print(f"       {arc}", file=sys.stderr)
    if args.update:
        BASELINE.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
