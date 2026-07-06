#!/bin/bash
# Usage:
#   bash scripts/run-tests-with-coverage.sh               # unit tests only (default)
#   bash scripts/run-tests-with-coverage.sh --e2e         # Playwright E2E tests only
#   bash scripts/run-tests-with-coverage.sh --all         # unit + E2E tests
#   bash scripts/run-tests-with-coverage.sh --durations   # unit tests + slowest 20 tests
set -e

DURATIONS_FLAG=""
ARGS=()
for arg in "$@"; do
  if [ "$arg" = "--durations" ]; then
    DURATIONS_FLAG="--durations=20"
  else
    ARGS+=("$arg")
  fi
done

if [ "${ARGS[0]:-}" = "--e2e" ]; then
  .venv/bin/pytest --e2e tests/e2e/ --override-ini='addopts='
elif [ "${ARGS[0]:-}" = "--all" ]; then
  .venv/bin/pytest \
    --cov=app \
    --cov-report=term-missing \
    --cov-report=html:htmlcov \
    --cov-report=xml:coverage.xml \
    --cov-config=.coveragerc \
    --cov-fail-under=100 \
    $DURATIONS_FLAG
  .venv/bin/pytest --e2e tests/e2e/ --override-ini='addopts='
else
  .venv/bin/pytest \
    --cov=app \
    --cov-report=term-missing \
    --cov-report=html:htmlcov \
    --cov-report=xml:coverage.xml \
    --cov-config=.coveragerc \
    --cov-fail-under=100 \
    $DURATIONS_FLAG
fi
