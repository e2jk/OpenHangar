#!/usr/bin/env bash
# docker/restore.sh — Restore an OpenHangar backup on the Docker host.
#
# Usage:
#   restore.sh <archive.zip.enc> [--upgrade-to=latest|vX.Y.Z|none] [--key-file=PATH]
#
# Options:
#   --upgrade-to=latest   (default) Pull the latest image after restore;
#                         Alembic migrations run automatically on startup.
#   --upgrade-to=vX.Y.Z  Upgrade to a specific released version after restore.
#   --upgrade-to=none     Restart the container without pulling a new image.
#   --key-file=PATH       Path to a file containing the decryption key (one line,
#                         no trailing newline required).  Omit to be prompted
#                         interactively (key is never stored in shell history).
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

# The decryption key (if any) is exported into the current process environment
# and passed to docker exec via "-e VARNAME" (name only, not "-e NAME=VALUE").
# This keeps the value out of process arguments (ps shows the name, not the
# value) and out of any temp file — it lives purely in process memory.
# _cleanup unsets it on any exit so it doesn't linger in the shell.
_RESTORE_KEY_EXPORTED=false
_cleanup() {
  if ${_RESTORE_KEY_EXPORTED}; then
    unset OPENHANGAR_RESTORE_ENCRYPTION_KEY 2>/dev/null || true
  fi
}
trap '_cleanup' EXIT
trap '_cleanup; log "ERROR: restore.sh exited unexpectedly at line ${LINENO}."' ERR

# ── Argument parsing ──────────────────────────────────────────────────────────
if [ $# -lt 1 ]; then
  echo "Usage: $(basename "$0") <archive.zip.enc> [--upgrade-to=latest|vX.Y.Z|none] [--key-file=PATH]"
  exit 1
fi

ARCHIVE_ARG="$1"
UPGRADE_TO="latest"
KEY_FILE=""
for arg in "$@"; do
  case "$arg" in
    --upgrade-to=*) UPGRADE_TO="${arg#*=}" ;;
    --key-file=*)   KEY_FILE="${arg#*=}" ;;
  esac
done

log "OpenHangar restore starting — archive: ${ARCHIVE_ARG}, upgrade-to: ${UPGRADE_TO}"

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
# Only acts when both versions are known semver values and they differ.
# Development containers use a local build that ignores the image tag in
# .env, so no version switch is attempted; Alembic handles the migration
# from the backup's schema to the current development head on startup.
#
PRE_RESTORE_SWITCHED=false
BACKUP_REGISTRY_IMAGE=""

if _is_semver "${BACKUP_VERSION}" && [ "${CURRENT_VERSION}" = "development" ]; then
  log "Development container detected — skipping version switch."
  log "Alembic will migrate from backup schema (${BACKUP_VERSION}) to development head on startup."
fi

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

# ── Decryption key: required for .enc archives ────────────────────────────────
# Key resolution order (highest to lowest priority):
#   1. --key-file=PATH                           file containing the raw key
#   2. OPENHANGAR_RESTORE_ENCRYPTION_KEY         in the current shell env
#   3. OPENHANGAR_RESTORE_ENCRYPTION_KEY         already set in the container
#      (e.g. via docker-compose environment: mapping from a host .env variable
#      like OPENHANGAR_PROD_BACKUP_ENCRYPTION_KEY — the docker exec below runs
#      without -e in this case, letting the container use its own env directly)
#   4. Interactive prompt                         typed once; never stored in history
#
# When the key comes from step 1 or 2 it is exported into the current process
# env and passed to the container via "docker exec -e VARNAME" (name only,
# not -e NAME=VALUE). ps(1) shows only the variable name — the value never
# appears in process args or on disk.  _cleanup() unsets it on any exit.
# OPENHANGAR_BACKUP_ENCRYPTION_KEY is never used for restoration.
if [[ "${ARCHIVE_NAME}" == *.enc ]]; then
  _KEY=""
  _USE_CONTAINER_ENV=false
  if [ -n "${KEY_FILE}" ]; then
    [ -f "${KEY_FILE}" ] || { log "ERROR: Key file not found: ${KEY_FILE}"; exit 1; }
    _KEY="$(tr -d '\n' < "${KEY_FILE}")"
  elif [ -n "${OPENHANGAR_RESTORE_ENCRYPTION_KEY:-}" ]; then
    _KEY="${OPENHANGAR_RESTORE_ENCRYPTION_KEY}"
    log "Using OPENHANGAR_RESTORE_ENCRYPTION_KEY from shell environment."
  elif docker exec "${CONTAINER}" sh -c 'test -n "${OPENHANGAR_RESTORE_ENCRYPTION_KEY:-}"' 2>/dev/null; then
    log "Using OPENHANGAR_RESTORE_ENCRYPTION_KEY from container environment."
    _USE_CONTAINER_ENV=true
  else
    read -rs -p "Decryption key for ${ARCHIVE_NAME} (input hidden, not stored in history): " _KEY </dev/tty
    echo >&2
  fi
  if [ -z "${_KEY}" ] && ! ${_USE_CONTAINER_ENV}; then
    log "ERROR: Archive is encrypted (.enc) but no decryption key was provided."
    log "       Use --key-file=PATH, set OPENHANGAR_RESTORE_ENCRYPTION_KEY, or"
    log "       type the key at the interactive prompt."
    exit 1
  fi
  if [ -n "${_KEY}" ]; then
    export OPENHANGAR_RESTORE_ENCRYPTION_KEY="${_KEY}"
    _RESTORE_KEY_EXPORTED=true
    _KEY=""  # clear from shell memory; value now lives only in process env
  fi
fi

# ── Restore via Flask CLI (decrypt + validate + psql + uploads) ───────────────
log "Applying backup ${ARCHIVE_NAME}..."
if ${_RESTORE_KEY_EXPORTED}; then
  # Pass the key by name only; docker exec reads the value from this process's
  # environment — the value is never part of the command-line arguments.
  docker exec -e OPENHANGAR_RESTORE_ENCRYPTION_KEY "${CONTAINER}" flask restore-backup "${CONTAINER_ARCHIVE_PATH}"
  unset OPENHANGAR_RESTORE_ENCRYPTION_KEY
  _RESTORE_KEY_EXPORTED=false
else
  docker exec "${CONTAINER}" flask restore-backup "${CONTAINER_ARCHIVE_PATH}"
fi
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
      --env-file "${ENV_FILE}" up -d --force-recreate "${SERVICE}"
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
      --env-file "${ENV_FILE}" up -d --force-recreate "${PULL_ARGS[@]}" "${SERVICE}"
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
      --env-file "${ENV_FILE}" up -d --force-recreate "${SERVICE}" \
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
