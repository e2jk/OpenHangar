#!/usr/bin/env bash
# demo/refresh.sh — wipe and refresh the OpenHangar demo instance.
#
# Recommended cron setup — run every 3 hours, through the launcher (which
# verifies the image's signature and runs this script out of it):
#   7 */3 * * * /opt/openhangar/openhangar-launcher.sh refresh >> /var/log/openhangar-demo.log 2>&1
#
# NOTE: All logic is wrapped in main() so that bash parses the entire function
# body before execution begins.  This prevents the "read past file offset" bug
# that occurs when the container start overwrites this bind-mounted script mid-
# run with a new (different-length) version.
#
set -euo pipefail

main() {

# Run by openhangar-launcher.sh from a temporary copy: it passes the folder
# holding .env.
SCRIPT_DIR="${OPENHANGAR_SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
# Walk up from the script's directory to find the .env file
COMPOSE_DIR="${SCRIPT_DIR}"
while [ "${COMPOSE_DIR}" != "/" ] && [ ! -f "${COMPOSE_DIR}/.env" ]; do
  COMPOSE_DIR="$(dirname "${COMPOSE_DIR}")"
done
[ -f "${COMPOSE_DIR}/.env" ] || { echo "ERROR: .env not found in any parent directory"; exit 1; }
ENV_FILE="$(readlink -f "${COMPOSE_DIR}/.env")"

# Read the container/service name from .env, fall back to the default if not set.
# Resolves one level of ${VAR} indirection (e.g. X=${Y}), as docker compose's
# own .env parser does.
_env_val() {
  local val
  val="$(grep -E "^${1}=" "${ENV_FILE}" 2>/dev/null | cut -d= -f2 | tr -d "\"'" | head -1)"
  if [[ "${val}" =~ ^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$ ]]; then
    val="$(_env_val "${BASH_REMATCH[1]}")"
  fi
  printf '%s' "${val}"
}
# The image the launcher pulled and verified, by digest: the demo runs exactly
# that, never whatever a tag points to by the time Compose looks it up.
IMAGE="${OPENHANGAR_VERIFIED_IMAGE:-}"
if [ -z "${IMAGE}" ]; then
  echo "ERROR: OPENHANGAR_VERIFIED_IMAGE is not set: run this through openhangar-launcher.sh refresh, which verifies the image first."
  exit 1
fi
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

# ── 2. Recreate the web container from the verified image ────────────────────
CURRENT=$(docker inspect --format '{{.Image}}' "${CONTAINER}" 2>/dev/null || echo "none")
NEW_ID=$(docker image inspect "${IMAGE}" --format '{{.Id}}' 2>/dev/null || echo "unknown")
# Recreating the container is the wipe: in demo mode, every start rebuilds the
# schema, reseeds the demo slots and reapplies the env-var settings
# (docker-init-db.py). --force-recreate makes sure it happens even when
# nothing in the config changed.
if [ "${CURRENT}" != "${NEW_ID}" ]; then
  log "New image ${IMAGE} -- recreating web container..."
else
  log "Image unchanged -- recreating web container..."
fi
# A variable set here overrides .env for Compose's interpolation.
OPENHANGAR_DEMO_IMAGE="${IMAGE}" docker compose --file "${COMPOSE_DIR}/docker-compose.yml" \
  --env-file "${ENV_FILE}" up -d --force-recreate "${SERVICE}"

# ── 3. Wait for the wiped and reseeded demo to serve again ───────────────────
# The reseed takes a few minutes on a small server. Docker marks the container
# unhealthy while it runs (the healthcheck's start period is shorter), so keep
# waiting through that until it turns healthy or the deadline passes. Don't
# run reset-db/seed-demo here: doing so while the start-up reseed is still
# running drops its tables and crashes it, which doubles the downtime.
WAIT_SECONDS=600
log "Waiting for ${CONTAINER} to be healthy (up to ${WAIT_SECONDS}s)..."
STATUS="starting"
for _ in $(seq 1 $((WAIT_SECONDS / 5))); do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' "${CONTAINER}" 2>/dev/null || echo "starting")
  if [ "${STATUS}" = "healthy" ]; then break; fi
  sleep 5
done
if [ "${STATUS}" != "healthy" ]; then
  log "ERROR: ${CONTAINER} not healthy after ${WAIT_SECONDS}s (last status: ${STATUS})"
  exit 1
fi
log "Demo wiped and reseeded."

# ── 4. Remove dangling images to free disk space ──────────────────────────────
log "Pruning unused Docker images..."
docker image prune -f
log "Docker image prune complete."

log "Demo refresh complete."

}

main "$@"
