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
# Version mismatch handling (requires a .meta sidecar next to the archive):
#   Backup older than container: the script temporarily switches the container
#     to the backup's version, restores, then applies --upgrade-to as normal.
#   Backup newer than container: the script upgrades the container to the
#     backup's version, restores, then applies --upgrade-to as normal.
#   In both cases the "restore version" image is pulled from the public registry
#     (ghcr.io/e2jk/openhangar:<backup-version>).
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

# ── .env image helpers ────────────────────────────────────────────────────────
# Captured once so _restore_env_image always knows what to put back.
ORIGINAL_IMAGE_LINE=$(grep "^OPENHANGAR_IMAGE=" "${ENV_FILE}" 2>/dev/null || echo "")

_set_env_image() {
  local img="$1"
  if grep -q "^OPENHANGAR_IMAGE=" "${ENV_FILE}"; then
    sed -i "s|^OPENHANGAR_IMAGE=.*|OPENHANGAR_IMAGE=${img}|" "${ENV_FILE}"
  else
    echo "OPENHANGAR_IMAGE=${img}" >> "${ENV_FILE}"
  fi
}

_restore_env_image() {
  if [ -n "${ORIGINAL_IMAGE_LINE}" ]; then
    sed -i "s|^OPENHANGAR_IMAGE=.*|${ORIGINAL_IMAGE_LINE}|" "${ENV_FILE}"
  else
    sed -i "/^OPENHANGAR_IMAGE=/d" "${ENV_FILE}"
  fi
}

# ── Wait for the container's Flask CLI to respond ────────────────────────────
_wait_for_ready() {
  local tries=0
  log "Waiting for container to be ready..."
  until docker exec "${CONTAINER}" flask check-empty-db >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ "${tries}" -ge 30 ]; then
      log "ERROR: Container did not become ready within 60 seconds."
      exit 1
    fi
    sleep 2
  done
}

# ── Semver helpers ───────────────────────────────────────────────────────────
_is_semver() {
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+].+)?$ ]]
}

_version_lt() {
  [ "$1" = "$2" ] && return 1
  [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$1" ]
}

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

# ── Development-version guard ─────────────────────────────────────────────────
#
# A "development" backup may contain an arbitrary unreleased schema; applying it
# to a versioned container is unsafe and is therefore refused outright.
# (The reverse — restoring a versioned backup onto a development container —
# is allowed, though Alembic will migrate as normal.)
#
if [ "${BACKUP_VERSION}" = "development" ] && _is_semver "${CURRENT_VERSION}"; then
  log "ERROR: This backup was created by a development build and cannot be"
  log "       restored onto a versioned container (${CURRENT_VERSION})."
  log "       Use a development container to restore a development backup."
  exit 1
fi

# ── Version compatibility: switch container to backup version if needed ───────
#
# We only act when both versions are known semver values (not "unknown" /
# "development") and they differ.  The backup version's image is always pulled
# from the public registry; the container is restarted at that version before
# the restore so Alembic creates the exact schema the backup expects.
# After the restore the normal --upgrade-to logic runs.
#
PRE_RESTORE_SWITCHED=false
BACKUP_REGISTRY_IMAGE=""

if _is_semver "${BACKUP_VERSION}" && _is_semver "${CURRENT_VERSION}" \
   && [ "${BACKUP_VERSION}" != "${CURRENT_VERSION}" ]; then

  BACKUP_REGISTRY_IMAGE="${IMAGE_BASE}:${BACKUP_VERSION}"

  if _version_lt "${BACKUP_VERSION}" "${CURRENT_VERSION}"; then
    log "Backup (${BACKUP_VERSION}) is older than container (${CURRENT_VERSION})."
    log "Temporarily switching container to ${BACKUP_VERSION} for restore;"
    log "  --upgrade-to=${UPGRADE_TO} will be applied afterward."
  else
    log "Backup (${BACKUP_VERSION}) is newer than container (${CURRENT_VERSION})."
    log "Upgrading container to ${BACKUP_VERSION} for restore."
  fi

  log "Pulling ${BACKUP_REGISTRY_IMAGE}..."
  if ! docker pull "${BACKUP_REGISTRY_IMAGE}"; then
    if _version_lt "${BACKUP_VERSION}" "${CURRENT_VERSION}"; then
      log "WARNING: Could not pull ${BACKUP_REGISTRY_IMAGE}."
      log "         Continuing with the current container (${CURRENT_VERSION}); schema"
      log "         compatibility is not guaranteed — restore may fail."
    else
      log "ERROR: Backup (${BACKUP_VERSION}) is newer than container (${CURRENT_VERSION})"
      log "       and pulling ${BACKUP_REGISTRY_IMAGE} failed."
      log "       Upgrade your container to ${BACKUP_VERSION} or newer, then retry."
      exit 1
    fi
  else
    _set_env_image "${BACKUP_REGISTRY_IMAGE}"
    docker compose --file "${COMPOSE_DIR}/docker-compose.yml" \
      --env-file "${ENV_FILE}" up -d "${SERVICE}" \
      || { _restore_env_image; exit 1; }
    PRE_RESTORE_SWITCHED=true
    _wait_for_ready
    CURRENT_VERSION="${BACKUP_VERSION}"
    log "Container is now running ${CURRENT_VERSION}."
  fi
fi

# ── Safety: ensure DB is empty ────────────────────────────────────────────────
log "Verifying database is empty..."
if ! docker exec "${CONTAINER}" flask check-empty-db; then
  log "ERROR: Database is not empty. Restore aborted to prevent data loss."
  log "       Start a fresh container (empty database) before running restore."
  if $PRE_RESTORE_SWITCHED; then _restore_env_image; fi
  exit 1
fi

# ── Restore via Flask CLI (decrypt + validate + psql + uploads) ───────────────
log "Applying backup ${ARCHIVE_NAME}..."
docker exec "${CONTAINER}" flask restore-backup "${CONTAINER_ARCHIVE_PATH}"
log "Backup applied."

# ── Post-restore: restart / upgrade to target version ────────────────────────
case "${UPGRADE_TO}" in
  none)
    if $PRE_RESTORE_SWITCHED; then
      log "Restarting container at backup version ${CURRENT_VERSION} (Alembic will run any pending migrations)..."
      # .env already has the backup-version image; leave it as the new baseline.
    else
      log "Restarting container with current image (Alembic will migrate on startup)..."
    fi
    docker compose --file "${COMPOSE_DIR}/docker-compose.yml" \
      --env-file "${ENV_FILE}" up -d "${SERVICE}"
    log "Done. Container is running."
    ;;
  latest)
    if $PRE_RESTORE_SWITCHED; then
      _restore_env_image  # undo backup-version override before pulling latest
    fi
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
    if $PRE_RESTORE_SWITCHED; then
      _restore_env_image  # undo backup-version override before applying target version
    fi
    TARGET_VERSION="${UPGRADE_TO#v}"
    if [ "${TARGET_VERSION}" = "development" ]; then
      log "ERROR: Cannot upgrade to 'development' — use 'none', 'latest', or a versioned tag (vX.Y.Z)."
      exit 1
    fi
    TARGET_IMAGE="${IMAGE_BASE}:${TARGET_VERSION}"
    log "Pulling ${TARGET_IMAGE}..."
    docker pull "${TARGET_IMAGE}"
    _set_env_image "${TARGET_IMAGE}"
    docker compose --file "${COMPOSE_DIR}/docker-compose.yml" \
      --env-file "${ENV_FILE}" up -d "${SERVICE}" \
      || { _restore_env_image; exit 1; }
    _restore_env_image
    log "Done. Container is running with ${TARGET_IMAGE}."
    ;;
  *)
    log "ERROR: Unknown --upgrade-to value: '${UPGRADE_TO}'"
    log "       Use: latest  |  vX.Y.Z  |  none"
    exit 1
    ;;
esac

log "Restore complete."
