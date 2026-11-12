#!/bin/bash
# Full runs remain the authoritative 100% line-coverage gate.
# Usage:
#   bash scripts/run-tests-with-coverage.sh
#   bash scripts/run-tests-with-coverage.sh --durations
#   bash scripts/run-tests-with-coverage.sh --all
#   bash scripts/run-tests-with-coverage.sh --e2e
#   bash scripts/run-tests-with-coverage.sh --targeted tests/test_example.py -k case
set -euo pipefail

MODE=full
case "${1:-}" in
  --all|--e2e|--targeted) MODE="${1#--}"; shift ;;
esac
ARGS=()
DURATIONS=()
for arg in "$@"; do
  if [ "$arg" = "--durations" ]; then
    DURATIONS=(--durations=20)
  elif [ "$MODE" = targeted ]; then
    case "$arg" in
      --cov*|--junitxml*|--junit-xml*)
        echo "Targeted runs do not write coverage or JUnit reports; run the full gate separately." >&2
        exit 2 ;;
    esac
    ARGS+=("$arg")
  else
    echo "Unexpected argument: $arg. Use --targeted before pytest selectors/options." >&2
    exit 2
  fi
done

if [ "$MODE" = targeted ]; then
  if [ "${#ARGS[@]}" -eq 0 ]; then
    echo "--targeted requires pytest selectors/options." >&2
    exit 2
  fi
  echo "Targeted validation only: no coverage report; the full 100% gate is still required."
  .venv/bin/pytest "${ARGS[@]}" "${DURATIONS[@]}" --no-cov
  exit
fi

if [ "$MODE" != e2e ]; then
  .venv/bin/pytest \
    --cov=app \
    --cov-report=term-missing \
    --cov-report=html:htmlcov \
    --cov-report=xml:coverage.xml \
    --cov-config=.coveragerc \
    --cov-fail-under=100 \
    --junitxml=test-results.xml \
    "${DURATIONS[@]}"
fi
if [ "$MODE" = e2e ] || [ "$MODE" = all ]; then
  .venv/bin/pytest --e2e tests/e2e/ --override-ini='addopts=' "${DURATIONS[@]}"
fi
