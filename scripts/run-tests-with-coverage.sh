#!/bin/bash
set -e
.venv/bin/pytest \
  --cov=app \
  --cov-report=term-missing \
  --cov-report=html:htmlcov \
  --cov-report=xml:coverage.xml \
  --cov-config=.coveragerc \
  --cov-fail-under=100
