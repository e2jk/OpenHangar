#!/usr/bin/env bash
# docker/upgrade.sh — pull the latest OpenHangar image and recreate the container.
#
# Recommended cron setup — run every minute to catch the trigger file promptly:
#   * * * * * [ -f /opt/openhangar/upgrade/upgrade.sh ] && \
#             cp /opt/openhangar/upgrade/upgrade.sh /opt/openhangar/upgrade.sh; \
#             [ -f /opt/openhangar/upgrade.sh ] && \
#             /opt/openhangar/upgrade.sh >> /var/log/openhangar-upgrade.log 2>&1
#
# Adapt the paths to match your OPENHANGAR_UPGRADE_DIR bind-mount source.
# The copy step keeps a stable fallback at the outer path: if the container is
# running the bind-mount copy is refreshed before each run; if the container is
# stopped the last-known copy is used instead.
# The script auto-detects which compose service owns the upgrade directory
# (safe when multiple OpenHangar instances share the same host) and reads the
# current image from the container itself — no hardcoded names or env-var keys.
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

# ── Parse arguments ────────────────────────────────────────────────────────────
DEBUG=0
for arg in "$@"; do
  case "$arg" in
    --debug) DEBUG=1 ;;
    *) echo "Unknown argument: ${arg}" >&2; exit 1 ;;
  esac
done

# Save fd 3 = original stderr so dbg() survives the ">>${LOG} 2>&1" redirect
# used around the pull/recreate block further below.
exec 3>&2
dbg() { [ "${DEBUG}" -eq 1 ] && echo "[DBG] $*" >&3 || true; }

# ── Locate script and upgrade directory ───────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dbg "SCRIPT_DIR : ${SCRIPT_DIR}"

# When run from the cron copy outside the bind-mount dir (e.g. prod/upgrade.sh
# instead of prod/upgrade/upgrade.sh), the trigger files live one level down.
if [ -d "${SCRIPT_DIR}/upgrade" ]; then
  UPGRADE_DIR="${SCRIPT_DIR}/upgrade"
  dbg "UPGRADE_DIR: ${UPGRADE_DIR} (upgrade/ subdirectory of SCRIPT_DIR)"
else
  UPGRADE_DIR="${SCRIPT_DIR}"
  dbg "UPGRADE_DIR: ${UPGRADE_DIR} (same as SCRIPT_DIR)"
fi

TRIGGER="${UPGRADE_DIR}/trigger"
RUNNING="${UPGRADE_DIR}/trigger.running"
DONE="${UPGRADE_DIR}/trigger.done"
FAILED="${UPGRADE_DIR}/trigger.failed"
LOG="${UPGRADE_DIR}/upgrade.log"

dbg "TRIGGER    : ${TRIGGER} ($([ -f "${TRIGGER}" ] && echo "FOUND" || echo "not found"))"

# Nothing to do if no trigger file exists
[ -f "${TRIGGER}" ] || { dbg "No trigger file — nothing to do."; exit 0; }

# Atomic rename prevents a second cron tick from double-triggering
mv "${TRIGGER}" "${RUNNING}"
dbg "Trigger renamed to trigger.running."

# ── Locate .env / docker-compose.yml ─────────────────────────────────────────
COMPOSE_DIR="${SCRIPT_DIR}"
while [ "${COMPOSE_DIR}" != "/" ] && [ ! -f "${COMPOSE_DIR}/.env" ]; do
  COMPOSE_DIR="$(dirname "${COMPOSE_DIR}")"
done
[ -f "${COMPOSE_DIR}/.env" ] || { echo "ERROR: .env not found in any parent directory"; exit 1; }
ENV_FILE="$(readlink -f "${COMPOSE_DIR}/.env")"
dbg "COMPOSE_DIR: ${COMPOSE_DIR}"
dbg "ENV_FILE   : ${ENV_FILE}"

# Read optional overrides from .env; returns empty string (not a failure) when
# the key is absent — grep exits 1 on no match and pipefail would abort otherwise.
_env_val() { grep -E "^${1}=" "${ENV_FILE}" 2>/dev/null | cut -d= -f2 | tr -d "\"'" | head -1 || true; }

# Auto-detect which compose service owns this upgrade dir by comparing the host-
# side mount source paths reported by docker inspect.  Matching on the host path
# (not the container-internal /data/upgrade) is essential when multiple
# OpenHangar instances run on the same host: each instance has a distinct source
# directory so the lookup is unambiguous.
_detect_service() {
  docker ps -a --format '{{.ID}}' 2>/dev/null | while read -r cid; do
    if docker inspect "$cid" \
        --format '{{range .Mounts}}{{.Source}}|{{end}}' 2>/dev/null \
        | tr '|' '\n' | grep -qxF "${UPGRADE_DIR}"; then
      docker inspect "$cid" \
        --format '{{index .Config.Labels "com.docker.compose.service"}}' 2>/dev/null
      return
    fi
  done
}

# Auto-detect the image used by a compose service from the container metadata.
_detect_image() {
  docker ps -a \
    --filter "label=com.docker.compose.service=${1}" \
    --format '{{.Image}}' 2>/dev/null | head -1
}

# ── Resolve SERVICE ───────────────────────────────────────────────────────────
SERVICE="$(_env_val OPENHANGAR_SERVICE)"
if [ -n "${SERVICE}" ]; then
  dbg "SERVICE    : ${SERVICE} (OPENHANGAR_SERVICE from .env)"
else
  SERVICE="$(_detect_service)"
  if [ -n "${SERVICE}" ]; then
    dbg "SERVICE    : ${SERVICE} (auto-detected via docker inspect mount source)"
  else
    SERVICE="openhangar-web"
    dbg "SERVICE    : ${SERVICE} (built-in default — detection found nothing)"
  fi
fi

# ── Resolve IMAGE ─────────────────────────────────────────────────────────────
IMAGE="$(_env_val OPENHANGAR_IMAGE)"
if [ -n "${IMAGE}" ]; then
  dbg "IMAGE      : ${IMAGE} (OPENHANGAR_IMAGE from .env)"
else
  IMAGE="$(_detect_image "${SERVICE}")"
  if [ -n "${IMAGE}" ]; then
    dbg "IMAGE      : ${IMAGE} (auto-detected from container)"
  else
    IMAGE="ghcr.io/e2jk/openhangar:latest"
    dbg "IMAGE      : ${IMAGE} (built-in default — detection found nothing)"
  fi
fi

dbg "LOG        : ${LOG}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

# ── Pull latest image ──────────────────────────────────────────────────────────
{
  log "Upgrade triggered."
  log "Pulling ${IMAGE}..."
  OLD_ID=$(docker image inspect "${IMAGE}" --format '{{.Id}}' 2>/dev/null || echo "none")
  dbg "Current image digest: ${OLD_ID:0:19}"
  if docker pull "${IMAGE}"; then
    NEW_ID=$(docker image inspect "${IMAGE}" --format '{{.Id}}' 2>/dev/null || echo "none")
    dbg "Pulled image digest : ${NEW_ID:0:19}"
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
