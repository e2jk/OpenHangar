#!/usr/bin/env bash
# docker/restore.sh — Restore an OpenHangar backup on the Docker host.
#
# Usage:
#   restore.sh <archive.zip.enc> [--upgrade-to=latest|vX.Y.Z|none]
#
# Options:
#   --upgrade-to=latest   (default) Pull the latest image after restore;
#                         Alembic migrations run automatically on startup.
#   --upgrade-to=vX.Y.Z  Upgrade to a specific released version after restore.
#   --upgrade-to=none     Restart the container without pulling a new image.
#
# The script must be run from the Docker host. It is bundled in the image and
# published to the backups folder (./openhangar/data/backups/restore.sh) at
# container startup so it is always up-to-date with the running version.
#
# Prerequisites: the target container must be running and its database must be
# empty (freshly started, no prior data). Restore into a non-empty database is
# refused automatically.
#
set -euo pipefail

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

# ── Argument parsing ──────────────────────────────────────────────────────────
if [ $# -lt 1 ]; then
  echo "Usage: $(basename "$0") <archive.zip.enc> [--upgrade-to=latest|vX.Y.Z|none]"
  exit 1
fi

ARCHIVE_ARG="$1"
UPGRADE_TO="latest"
for arg in "$@"; do
  case "$arg" in
    --upgrade-to=*) UPGRADE_TO="${arg#*=}" ;;
  esac
done

log "OpenHangar restore starting — archive: ${ARCHIVE_ARG}, upgrade-to: ${UPGRADE_TO}"
trap 'log "ERROR: restore.sh exited unexpectedly at line ${LINENO}."' ERR

# ── Locate .env: walk up from script dir, then from CWD ──────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_find_env() {
  local dir="$1"
  while [ "${dir}" != "/" ]; do
    [ -f "${dir}/.env" ] && { echo "${dir}"; return 0; }
    dir="$(dirname "${dir}")"
  done
  return 1
}
COMPOSE_DIR="$(_find_env "${SCRIPT_DIR}" || _find_env "$(pwd)" || true)"
[ -n "${COMPOSE_DIR}" ] || { log "ERROR: .env not found searching up from ${SCRIPT_DIR} or $(pwd)"; exit 1; }
ENV_FILE="${COMPOSE_DIR}/.env"
log "Configuration: ${ENV_FILE}"

_env_val() { grep -E "^${1}=" "${ENV_FILE}" 2>/dev/null | cut -d= -f2 | tr -d "\"'" | head -1 || true; }
IMAGE="$(_env_val OPENHANGAR_IMAGE)"
IMAGE="${IMAGE:-$(_env_val OPENHANGAR_DEMO_IMAGE)}"
IMAGE="${IMAGE:-ghcr.io/e2jk/openhangar:latest}"
IMAGE_BASE="${IMAGE%:*}"
CONTAINER="$(_env_val OPENHANGAR_WEB_CONTAINER)"
CONTAINER="${CONTAINER:-$(_env_val OPENHANGAR_DEMO_WEB_CONTAINER)}"
CONTAINER="${CONTAINER:-openhangar-web}"
SERVICE="${CONTAINER}"

# ── Resolve archive path ──────────────────────────────────────────────────────
if [[ "${ARCHIVE_ARG}" != /* ]]; then
  ARCHIVE="$(pwd)/${ARCHIVE_ARG}"
else
  ARCHIVE="${ARCHIVE_ARG}"
fi
[ -f "${ARCHIVE}" ] || { log "ERROR: Archive not found: ${ARCHIVE}"; exit 1; }
ARCHIVE_NAME="$(basename "${ARCHIVE}")"
CONTAINER_ARCHIVE_PATH="/data/backups/${ARCHIVE_NAME}"

# ── Read backup metadata from sidecar ────────────────────────────────────────
META_PATH="${ARCHIVE%.zip.enc}.meta"
BACKUP_VERSION="unknown"
BACKUP_ALEMBIC="unknown"
if [ -f "${META_PATH}" ]; then
  BACKUP_VERSION=$(python3 -c "import json; d=json.load(open('${META_PATH}')); print(d.get('app_version','unknown'))" 2>/dev/null || echo "unknown")
  BACKUP_ALEMBIC=$(python3 -c "import json; d=json.load(open('${META_PATH}')); print(d.get('alembic_head') or 'unknown')" 2>/dev/null || echo "unknown")
  log "Backup metadata: version=${BACKUP_VERSION}  alembic=${BACKUP_ALEMBIC}"
else
  log "WARNING: No metadata sidecar found (${META_PATH}) — version check will be done inside the container."
fi

# ── Get current container version ────────────────────────────────────────────
CURRENT_VERSION=$(docker exec "${CONTAINER}" printenv OPENHANGAR_VERSION 2>/dev/null || echo "unknown")
log "Container version: ${CURRENT_VERSION}"

if [ "${BACKUP_VERSION}" != "unknown" ] && [ "${BACKUP_VERSION}" != "${CURRENT_VERSION}" ]; then
  log "NOTE: Backup version (${BACKUP_VERSION}) differs from container version (${CURRENT_VERSION})."
  log "      The container will validate migration compatibility before restoring."
fi

# ── Safety: ensure DB is empty ────────────────────────────────────────────────
log "Verifying database is empty..."
if ! docker exec "${CONTAINER}" flask check-empty-db; then
  log "ERROR: Database is not empty. Restore aborted to prevent data loss."
  log "       Start a fresh container (empty database) before running restore."
  exit 1
fi

# ── Restore via Flask CLI (decrypt + validate + psql + uploads) ───────────────
log "Applying backup ${ARCHIVE_NAME}..."
docker exec "${CONTAINER}" flask restore-backup "${CONTAINER_ARCHIVE_PATH}"
log "Backup applied."

# ── Post-restore: upgrade to target version ───────────────────────────────────
case "${UPGRADE_TO}" in
  none)
    log "Restarting container with current image (Alembic will migrate on startup)..."
    docker compose --file "${COMPOSE_DIR}/docker-compose.yml" \
      --env-file "${ENV_FILE}" up -d "${SERVICE}"
    log "Done. Container is running."
    ;;
  latest)
    # Registry deployments (OPENHANGAR_IMAGE set): pull the latest tag.
    # Local-build deployments (no registry image in .env): rebuild from source.
    if [ -n "$(_env_val OPENHANGAR_IMAGE)" ] || [ -n "$(_env_val OPENHANGAR_DEMO_IMAGE)" ]; then
      log "Restarting container with latest registry image (Alembic will migrate on startup)..."
      PULL_ARGS=("--pull" "always")
    else
      log "Rebuilding and restarting container from local source (Alembic will migrate on startup)..."
      PULL_ARGS=("--build")
    fi
    docker compose --file "${COMPOSE_DIR}/docker-compose.yml" \
      --env-file "${ENV_FILE}" up -d "${PULL_ARGS[@]}" "${SERVICE}"
    log "Done. Container is running."
    ;;
  v*)
    TARGET_VERSION="${UPGRADE_TO#v}"
    TARGET_IMAGE="${IMAGE_BASE}:${TARGET_VERSION}"
    log "Pulling ${TARGET_IMAGE}..."
    docker pull "${TARGET_IMAGE}"

    # Temporarily set the image in .env so docker-compose picks it up
    ORIGINAL_IMAGE_LINE=$(grep "^OPENHANGAR_IMAGE=" "${ENV_FILE}" 2>/dev/null || echo "")
    _set_image() {
      if grep -q "^OPENHANGAR_IMAGE=" "${ENV_FILE}"; then
        sed -i "s|^OPENHANGAR_IMAGE=.*|OPENHANGAR_IMAGE=${TARGET_IMAGE}|" "${ENV_FILE}"
      else
        echo "OPENHANGAR_IMAGE=${TARGET_IMAGE}" >> "${ENV_FILE}"
      fi
    }
    _restore_image() {
      if [ -n "${ORIGINAL_IMAGE_LINE}" ]; then
        sed -i "s|^OPENHANGAR_IMAGE=.*|${ORIGINAL_IMAGE_LINE}|" "${ENV_FILE}"
      else
        sed -i "/^OPENHANGAR_IMAGE=/d" "${ENV_FILE}"
      fi
    }
    _set_image
    docker compose --file "${COMPOSE_DIR}/docker-compose.yml" \
      --env-file "${ENV_FILE}" up -d "${SERVICE}" || { _restore_image; exit 1; }
    _restore_image
    log "Done. Container is running with ${TARGET_IMAGE}."
    ;;
  *)
    log "ERROR: Unknown --upgrade-to value: '${UPGRADE_TO}'"
    log "       Use: latest  |  vX.Y.Z  |  none"
    exit 1
    ;;
esac

log "Restore complete."
