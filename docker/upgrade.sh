#!/usr/bin/env bash
# docker/upgrade.sh — pull the latest OpenHangar image and recreate the container.
#
# Recommended cron setup — run every minute to catch the trigger file promptly:
#   * * * * * [ -f /opt/openhangar/openhangar/data/upgrade/upgrade.sh ] && \
#             cp /opt/openhangar/openhangar/data/upgrade/upgrade.sh /opt/openhangar/upgrade.sh; \
#             /opt/openhangar/upgrade.sh >> /var/log/openhangar-upgrade.log 2>&1
#
# The copy step keeps a stable fallback at the outer path: if the container is
# running the bind-mount copy is refreshed before each run; if the container is
# stopped the last-known copy is used instead.
#
# The script is bundled inside the Docker image and exported to the host via a
# bind-mount (see docker-compose.yml).  After each image update the host copy is
# replaced on the next container start, so the cron job picks up the latest
# version one run later.
#
# NOTE: All logic is wrapped in main() so that bash parses the entire function
# body before execution begins.  This prevents the "read past file offset" bug
# that occurs when the container start overwrites this bind-mounted script mid-
# run with a new (different-length) version.
#
set -euo pipefail

main() {

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPGRADE_DIR="${SCRIPT_DIR}"
TRIGGER="${UPGRADE_DIR}/trigger"
RUNNING="${UPGRADE_DIR}/trigger.running"
DONE="${UPGRADE_DIR}/trigger.done"
FAILED="${UPGRADE_DIR}/trigger.failed"
LOG="${UPGRADE_DIR}/upgrade.log"

# Nothing to do if no trigger file exists
[ -f "${TRIGGER}" ] || exit 0

# Atomic rename prevents a second cron tick from double-triggering
mv "${TRIGGER}" "${RUNNING}"

# Walk up from the script's directory to find the .env / docker-compose.yml
COMPOSE_DIR="${SCRIPT_DIR}"
while [ "${COMPOSE_DIR}" != "/" ] && [ ! -f "${COMPOSE_DIR}/.env" ]; do
  COMPOSE_DIR="$(dirname "${COMPOSE_DIR}")"
done
[ -f "${COMPOSE_DIR}/.env" ] || { echo "ERROR: .env not found in any parent directory"; exit 1; }
ENV_FILE="$(readlink -f "${COMPOSE_DIR}/.env")"

# Read image and service name from .env, fall back to defaults if not set
_env_val() { grep -E "^${1}=" "${ENV_FILE}" 2>/dev/null | cut -d= -f2 | tr -d "\"'" | head -1; }
IMAGE="$(_env_val OPENHANGAR_IMAGE)"
IMAGE="${IMAGE:-ghcr.io/e2jk/openhangar:latest}"
SERVICE="openhangar-web"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

# ── Pull latest image ──────────────────────────────────────────────────────────
{
  log "Upgrade triggered."
  log "Pulling ${IMAGE}..."
  OLD_ID=$(docker image inspect "${IMAGE}" --format '{{.Id}}' 2>/dev/null || echo "none")
  if docker pull "${IMAGE}"; then
    NEW_ID=$(docker image inspect "${IMAGE}" --format '{{.Id}}' 2>/dev/null || echo "none")
    if [ "${OLD_ID}" != "${NEW_ID}" ]; then
      log "New image pulled (${NEW_ID:0:19})."
    else
      log "Image unchanged — recreating container to pick up any config changes."
    fi
  else
    log "WARNING: Image pull failed — continuing with existing image."
  fi

  # ── Recreate the web container ──────────────────────────────────────────────
  log "Recreating ${SERVICE}..."
  docker compose --file "${COMPOSE_DIR}/docker-compose.yml" \
    --env-file "${ENV_FILE}" up -d --force-recreate "${SERVICE}"
  log "Upgrade complete."
} >> "${LOG}" 2>&1 \
  && echo "ok $(date -u +%FT%TZ)" > "${DONE}" \
  || echo "fail $(date -u +%FT%TZ)" > "${FAILED}"

rm -f "${RUNNING}"

# ── Remove dangling images to free disk space ──────────────────────────────────
docker image prune -f >> "${LOG}" 2>&1 || true

}

main "$@"
