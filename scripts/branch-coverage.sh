#!/bin/bash
# Branch coverage report (audit T01) — separate from, and never replacing, the
# authoritative 100% line gate (scripts/run-tests-with-coverage.sh).
#
# Runs the suite with branch measurement, writes htmlcov-branches/ and
# coverage-branches.xml (missing arcs per file), then checks the ratchet:
# the security- and money-critical modules listed in
# scripts/branch-coverage-baseline.json must not fall below their recorded
# branch coverage. Raise a module's baseline when its tests improve
# (`--update-baseline`); never lower it to make this pass.
#
# Usage:
#   bash scripts/branch-coverage.sh
#   bash scripts/branch-coverage.sh --update-baseline
set -euo pipefail

UPDATE=()
case "${1:-}" in
  "") ;;
  --update-baseline) UPDATE=(--update) ;;
  *) echo "Unexpected argument: $1" >&2; exit 2 ;;
esac

.venv/bin/pytest \
  --cov=app \
  --cov-branch \
  --cov-config=.coveragerc \
  --cov-report=html:htmlcov-branches \
  --cov-report=xml:coverage-branches.xml \
  --cov-report=
.venv/bin/python scripts/check_branch_coverage.py coverage-branches.xml "${UPDATE[@]}"
