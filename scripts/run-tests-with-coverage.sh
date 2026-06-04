#!/bin/bash
# Usage:
#   bash scripts/run-tests-with-coverage.sh          # unit tests only (default)
#   bash scripts/run-tests-with-coverage.sh --e2e    # Playwright E2E tests only
#   bash scripts/run-tests-with-coverage.sh --all    # unit + E2E tests
set -e

if [ "${1:-}" = "--e2e" ]; then
  .venv/bin/pytest --e2e tests/e2e/ --override-ini='addopts='
elif [ "${1:-}" = "--all" ]; then
  .venv/bin/pytest \
    --cov=app \
    --cov-report=term-missing \
    --cov-report=html:htmlcov \
    --cov-report=xml:coverage.xml \
    --cov-config=.coveragerc \
    --cov-fail-under=100
  .venv/bin/pytest --e2e tests/e2e/ --override-ini='addopts='
else
  .venv/bin/pytest \
    --cov=app \
    --cov-report=term-missing \
    --cov-report=html:htmlcov \
    --cov-report=xml:coverage.xml \
    --cov-config=.coveragerc \
    --cov-fail-under=100
fi
