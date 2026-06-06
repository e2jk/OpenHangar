#!/bin/bash
# Refresh tests/e2e/routes.json and tests/e2e/seed.json from the database
# of a running Docker dev instance.
#
# The script is piped via stdin into the web container where PostgreSQL is
# reachable on the internal Docker network — no port exposure needed.
#
# Usage:
#   bash scripts/refresh-e2e-sitemap.sh [<container-name>]
#
# If no container name is given, the script auto-detects the running
# OpenHangar web container (the first container whose image name contains
# "openhangar" and is not the database container).
set -e

CONTAINER="${1:-}"
if [ -z "$CONTAINER" ]; then
  CONTAINER=$(docker ps --format '{{.Names}}\t{{.Image}}' \
    | grep openhangar | grep -v postgres | grep -v db \
    | awk '{print $1}' | head -1)
fi

if [ -z "$CONTAINER" ]; then
  echo "ERROR: no running OpenHangar container found. Pass the container name as an argument." >&2
  exit 1
fi

echo "Piping generate_routes.py into container '$CONTAINER'…"
docker exec -i "$CONTAINER" python - \
  --out /tmp/e2e_routes.json \
  --seed-out /tmp/e2e_seed.json \
  < scripts/generate_routes.py

docker cp "$CONTAINER:/tmp/e2e_routes.json" tests/e2e/routes.json
docker cp "$CONTAINER:/tmp/e2e_seed.json" tests/e2e/seed.json

echo "Written tests/e2e/routes.json and tests/e2e/seed.json"
