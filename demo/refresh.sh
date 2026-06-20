#!/usr/bin/env bash
# demo/refresh.sh — wipe and refresh the OpenHangar demo instance.
#
# Recommended cron setup — run every 3 hours:
#   7 */3 * * * [ -f /opt/openhangar/refresh/refresh.sh ] && cp /opt/openhangar/refresh/refresh.sh /opt/openhangar/refresh.sh; /opt/openhangar/refresh.sh >> /var/log/openhangar-demo.log 2>&1
#
# The one-liner keeps /opt/openhangar/refresh.sh as a stable fallback: if the
# container is running the bind-mount copy is refreshed before each run; if the
# container is stopped the last-known copy is used instead.
#
# The script is bundled inside the Docker image and exported to the host via a
# bind-mount (see docker-compose.yml: /opt/openhangar/refresh).  After each
# image update the host copy is replaced on the next container start, so the
# cron job automatically picks up the latest version one run later.
#
# NOTE: All logic is wrapped in main() so that bash parses the entire function
# body before execution begins.  This prevents the "read past file offset" bug
# that occurs when the container start overwrites this bind-mounted script mid-
# run with a new (different-length) version.
#
set -euo pipefail

main() {

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Walk up from the script's directory to find the .env file
COMPOSE_DIR="${SCRIPT_DIR}"
while [ "${COMPOSE_DIR}" != "/" ] && [ ! -f "${COMPOSE_DIR}/.env" ]; do
  COMPOSE_DIR="$(dirname "${COMPOSE_DIR}")"
done
[ -f "${COMPOSE_DIR}/.env" ] || { echo "ERROR: .env not found in any parent directory"; exit 1; }
ENV_FILE="$(readlink -f "${COMPOSE_DIR}/.env")"

# Read image and container/service name from .env, fall back to defaults if not set
_env_val() { grep -E "^${1}=" "${ENV_FILE}" 2>/dev/null | cut -d= -f2 | tr -d "\"'" | head -1; }
IMAGE="$(_env_val OPENHANGAR_DEMO_IMAGE)"
IMAGE="${IMAGE:-ghcr.io/e2jk/openhangar:latest}"
CONTAINER="$(_env_val OPENHANGAR_DEMO_WEB_CONTAINER)"
CONTAINER="${CONTAINER:-openhangar-demo-web}"
SERVICE="${CONTAINER}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

# ── 1. Compute and write next wipe timestamp ──────────────────────────────────
NEXT_WIPE=$(date -u -d "+3 hours" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null \
  || date -u -v+3H +"%Y-%m-%dT%H:%M:%SZ")  # macOS fallback
log "Next wipe scheduled at: ${NEXT_WIPE}"

# Update OPENHANGAR_DEMO_NEXT_WIPE_UTC in the .env file (create the line if absent)
if grep -q "^OPENHANGAR_DEMO_NEXT_WIPE_UTC=" "${ENV_FILE}"; then
  sed -i "s|^OPENHANGAR_DEMO_NEXT_WIPE_UTC=.*|OPENHANGAR_DEMO_NEXT_WIPE_UTC=${NEXT_WIPE}|" "${ENV_FILE}"
else
  echo "OPENHANGAR_DEMO_NEXT_WIPE_UTC=${NEXT_WIPE}" >> "${ENV_FILE}"
fi

# ── 2. Pull latest image and detect if it changed ────────────────────────────
log "Pulling latest image..."
OLD_ID=$(docker image inspect "${IMAGE}" --format '{{.Id}}' 2>/dev/null || echo "none")
if docker pull --quiet "${IMAGE}"; then
  NEW_ID=$(docker image inspect "${IMAGE}" --format '{{.Id}}' 2>/dev/null || echo "none")
else
  log "WARNING: Image pull failed -- continuing with existing image"
  NEW_ID="${OLD_ID}"
fi

if [ "${OLD_ID}" != "${NEW_ID}" ]; then
  log "New image detected -- recreating web container..."
  docker compose --file "${COMPOSE_DIR}/docker-compose.yml" \
    --env-file "${ENV_FILE}" up -d --pull always "${SERVICE}"
else
  log "Image unchanged -- recreating web container to apply updated config..."
  docker compose --file "${COMPOSE_DIR}/docker-compose.yml" \
    --env-file "${ENV_FILE}" up -d "${SERVICE}"
fi

# ── 3. Reset schema and reseed demo slots ────────────────────────────────────
# Wait for the container to be healthy before seeding
log "Waiting for ${CONTAINER} to be healthy..."
for i in $(seq 1 30); do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' "${CONTAINER}" 2>/dev/null || echo "starting")
  if [ "${STATUS}" = "healthy" ]; then break; fi
  sleep 2
done

log "Resetting database schema..."
docker exec "${CONTAINER}" flask reset-db
log "Reseeding demo slots..."
docker exec "${CONTAINER}" flask seed-demo

# ── 4. Remove dangling images to free disk space ──────────────────────────────
log "Pruning unused Docker images..."
docker image prune -f
log "Docker image prune complete."

log "Demo refresh complete."

}

main "$@"
