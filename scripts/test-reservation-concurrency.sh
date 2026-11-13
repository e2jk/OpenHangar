#!/usr/bin/env bash
# Separate PostgreSQL lane; the full 100% SQLite coverage gate remains required.
set -euo pipefail
: "${TEST_POSTGRES_URL:?Set a disposable PostgreSQL URL}"
.venv/bin/pytest tests/test_reservation_concurrency_postgres.py \
  tests/test_billing_concurrency_postgres.py -o addopts='' -v "$@"
