#!/usr/bin/env bash
# .github/scripts/smoke_test.sh <image-tag>
#
# Boots <image-tag> in demo mode against a disposable Postgres and waits up
# to 120s for its HEALTHCHECK to report healthy. Leaves the containers/
# network running on success — docker-validate's ZAP scan targets the still-
# running web container; other callers that don't need that should tear
# down explicitly afterward. Exits non-zero and dumps container logs on
# failure.
set -euo pipefail

IMAGE="${1:?Usage: smoke_test.sh <image-tag>}"
SMOKE_DB="smoke-db"
SMOKE_DB_NAME="smokedb"
SMOKE_DB_USER="smokeuser"
SMOKE_DB_PASS="smokepass"
SMOKE_WEB="smoke-web"

docker network create smoke-net
docker run -d --name "$SMOKE_DB" --network smoke-net \
  -e POSTGRES_DB="$SMOKE_DB_NAME" \
  -e POSTGRES_USER="$SMOKE_DB_USER" \
  -e POSTGRES_PASSWORD="$SMOKE_DB_PASS" \
  postgres:18-alpine

docker run -d --name "$SMOKE_WEB" --network smoke-net \
  -p 5001:5000 \
  -e OPENHANGAR_DATABASE_URL="postgresql://$SMOKE_DB_USER:$SMOKE_DB_PASS@$SMOKE_DB:5432/$SMOKE_DB_NAME" \
  -e OPENHANGAR_DB_HOST="$SMOKE_DB" \
  -e OPENHANGAR_ENV=demo \
  -e OPENHANGAR_SECRET_KEY=smoke-ci-secret-not-for-production-use-only \
  "$IMAGE"

echo "Waiting up to 120 s for $SMOKE_WEB to become healthy..."
start=$SECONDS
deadline=$((start + 120))
while [ $SECONDS -lt $deadline ]; do
  status=$(docker inspect --format='{{.State.Health.Status}}' "$SMOKE_WEB")
  echo "  [t+$((SECONDS - start))s] health=${status}"
  [ "$status" = "healthy" ] && break
  [ "$status" = "unhealthy" ] && break
  sleep 1
done

final=$(docker inspect --format='{{.State.Health.Status}}' "$SMOKE_WEB")
if [ "$final" != "healthy" ]; then
  echo "::error::$SMOKE_WEB did not become healthy within 120 s (final status: ${final})"
  echo "=== $SMOKE_WEB logs ==="
  docker logs "$SMOKE_WEB"
  exit 1
fi
echo "Smoke test passed — container is healthy."
