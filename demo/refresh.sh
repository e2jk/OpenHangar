#!/usr/bin/env bash
# demo/refresh.sh — wipe and refresh the OpenHangar demo instance.
# Run every 3 hours via cron:
#   7 */3 * * * /opt/openhangar/demo/refresh.sh >> /var/log/openhangar-demo.log 2>&1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="ghcr.io/e2jk/openhangar:latest"
CONTAINER="openhangar-demo-web"
SERVICE="${CONTAINER}"
ENV_FILE="${SCRIPT_DIR}/.env"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

# ── 1. Compute and write next wipe timestamp ──────────────────────────────────
NEXT_WIPE=$(date -u -d "+3 hours" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null \
  || date -u -v+3H +"%Y-%m-%dT%H:%M:%SZ")  # macOS fallback
log "Next wipe scheduled at: ${NEXT_WIPE}"

# Update DEMO_NEXT_WIPE_UTC in the .env file (create the line if absent)
if grep -q "^DEMO_NEXT_WIPE_UTC=" "${ENV_FILE}"; then
  sed -i "s|^DEMO_NEXT_WIPE_UTC=.*|DEMO_NEXT_WIPE_UTC=${NEXT_WIPE}|" "${ENV_FILE}"
else
  echo "DEMO_NEXT_WIPE_UTC=${NEXT_WIPE}" >> "${ENV_FILE}"
fi

# ── 2. Pull latest image and detect if it changed ────────────────────────────
log "Pulling latest image..."
OLD_ID=$(docker image inspect "${IMAGE}" --format '{{.Id}}' 2>/dev/null || echo "none")
docker pull --quiet "${IMAGE}"
NEW_ID=$(docker image inspect "${IMAGE}" --format '{{.Id}}' 2>/dev/null || echo "none")

if [ "${OLD_ID}" != "${NEW_ID}" ]; then
  log "New image detected — recreating web container..."
  docker compose --file "${SCRIPT_DIR}/docker-compose.yml" \
    --env-file "${ENV_FILE}" up -d --pull always "${SERVICE}"
else
  log "Image unchanged — restarting app container only..."
  docker restart "${CONTAINER}"
fi

# Wait for the container to be healthy before seeding
log "Waiting for ${CONTAINER} to be healthy..."
for i in $(seq 1 30); do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' "${CONTAINER}" 2>/dev/null || echo "starting")
  if [ "${STATUS}" = "healthy" ]; then break; fi
  sleep 2
done

# ── 3. Wipe and reseed demo slots ─────────────────────────────────────────────
log "Reseeding demo slots..."
docker exec "${CONTAINER}" flask seed-demo

log "Demo refresh complete."
