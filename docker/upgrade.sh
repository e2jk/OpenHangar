#!/usr/bin/env bash
# docker/upgrade.sh — pull the latest OpenHangar image and recreate the container.
#
# Recommended cron setup — run every minute to catch the trigger file promptly:
#   * * * * * [ -f /opt/openhangar/upgrade/upgrade.sh ] && \
#             cp /opt/openhangar/upgrade/upgrade.sh /opt/openhangar/upgrade.sh; \
#             [ -f /opt/openhangar/upgrade.sh ] && \
#             /opt/openhangar/upgrade.sh >> /opt/openhangar/upgrade/upgrade.log 2>&1
#
# Point the outer redirect at upgrade.log inside the bind-mount dir itself
# (not a separate file): the script already writes its own detailed log there,
# so the outer redirect only ever adds lines for failures that happen before
# that point (missing .env, bad argument) — keeping everything in one place
# instead of leaving a second, usually-empty log file to be confused with.
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

# A bare 12- or 64-char hex string is a Docker image ID, never a real
# "repo:tag" reference. `docker ps`/`{{.Image}}` reports the original tag for
# a container only as long as that tag still points, locally, to the exact
# image the container is running — if some other process on the host (e.g.
# a second OpenHangar instance sharing the same image tag) has since moved
# the tag to a different digest, Docker silently reports the bare image ID
# instead, which `docker pull` cannot resolve against a registry.
_looks_like_image_ref() {
  [[ -n "$1" && ! "$1" =~ ^[0-9a-f]{12}$ && ! "$1" =~ ^[0-9a-f]{64}$ ]]
}

# Auto-detect the image used by a compose service from the container metadata.
_detect_image() {
  local ref
  ref="$(docker ps -a \
    --filter "label=com.docker.compose.service=${1}" \
    --format '{{.Image}}' 2>/dev/null | head -1)"
  _looks_like_image_ref "${ref}" && echo "${ref}"
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
    dbg "IMAGE      : ${IMAGE} (built-in default — detection found nothing usable)"
  fi
fi

dbg "LOG        : ${LOG}"

# In debug mode, log() writes to both the log file (via the block redirect) and
# the terminal (fd 3 = saved original stderr).
if [ "${DEBUG}" -eq 1 ]; then
  log() { local m="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; echo "${m}"; echo "${m}" >&3; }
else
  log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }
fi

# cosign verifies the SHA256 *digest* CI signed, never the floating tag —
# verifying a tag would leave a window (docker pull ... resolve digest ...
# verify) where the registry could serve a different image between the pull
# and the check. --certificate-identity-regexp pins verification to this
# repo's own ci.yml workflow: publishing happens either from a version-tag
# push (refs/tags/v*) or from the pull_request run that ci.yml's own
# publish_now logic gates to the ship/dependabot/renovate branches (the
# OIDC certificate encodes that as refs/pull/<N>/merge — the PR number, not
# the source branch name, since GitHub doesn't expose the head ref in the
# SAN). refs/heads/main is never a signing ref: merging to main is a no-op,
# the image was already published before merge. A signature from anywhere
# else (a fork, a different workflow, a compromised registry credential
# pushing under a stolen identity) is refused.
_verify_image_signature() {
  local image_ref="$1"
  # cron runs this script with a minimal PATH (typically just /usr/bin:/bin)
  # that usually excludes /usr/local/bin — where cosign ends up per our own
  # install instructions (docs/self-hosting.md) and most manual-install
  # guides upstream. Rather than requiring every self-hoster to edit their
  # crontab's PATH, fall back to that one well-known absolute path before
  # giving up. Deliberately narrow (not a broad multi-directory search):
  # this is the exact location we tell people to install it to, so it's the
  # only fallback that's actually load-bearing on our own documentation.
  local cosign_bin="cosign"
  if ! command -v cosign >/dev/null 2>&1; then
    if [ -x /usr/local/bin/cosign ]; then
      cosign_bin="/usr/local/bin/cosign"
    else
      log "WARNING: cosign is not installed — the image signature was NOT verified before this upgrade."
      log "WARNING: install cosign (https://docs.sigstore.dev/cosign/system_config/installation/) so future upgrades can verify it."
      return 0
    fi
  fi
  local digest_ref
  digest_ref="$(docker image inspect "${image_ref}" --format '{{index .RepoDigests 0}}' 2>/dev/null || echo "")"
  if [ -z "${digest_ref}" ]; then
    log "WARNING: could not resolve a digest reference for ${image_ref} — skipping signature verification."
    return 0
  fi
  log "Verifying image signature (cosign) for ${digest_ref}..."
  if "${cosign_bin}" verify \
      --certificate-oidc-issuer https://token.actions.githubusercontent.com \
      --certificate-identity-regexp '^https://github\.com/e2jk/OpenHangar/\.github/workflows/ci\.yml@refs/(pull/[0-9]+/merge|tags/v.*)$' \
      "${digest_ref}" >/dev/null 2>&1; then
    log "Signature verified."
    return 0
  fi
  log "ERROR: cosign signature verification FAILED for ${digest_ref} — this image was not signed by this repository's CI. Aborting upgrade; the currently running container is left untouched."
  return 1
}

# ── Pull latest image ──────────────────────────────────────────────────────────
RECREATE_AT=""
{
  log "Upgrade triggered."
  log "Pulling ${IMAGE}..."
  OLD_ID=$(docker image inspect "${IMAGE}" --format '{{.Id}}' 2>/dev/null || echo "none")
  dbg "Current image digest: ${OLD_ID:0:19}"
  VERIFIED=true
  if docker pull "${IMAGE}"; then
    NEW_ID=$(docker image inspect "${IMAGE}" --format '{{.Id}}' 2>/dev/null || echo "none")
    dbg "Pulled image digest : ${NEW_ID:0:19}"
    if [ "${OLD_ID}" != "${NEW_ID}" ]; then
      log "New image pulled (${NEW_ID:0:19})."
    else
      log "Image unchanged — recreating container to pick up any config changes."
    fi
    _verify_image_signature "${IMAGE}" || VERIFIED=false
  else
    log "WARNING: Image pull failed — continuing with existing image."
  fi

  # ── Recreate the web container ──────────────────────────────────────────────
  if [ "${VERIFIED}" = "true" ]; then
    RECREATE_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    log "Recreating ${SERVICE}..."
    docker compose --file "${COMPOSE_DIR}/docker-compose.yml" \
      --env-file "${ENV_FILE}" up -d --force-recreate "${SERVICE}"
    log "Upgrade complete."
  else
    log "Upgrade aborted."
    false
  fi
} >> "${LOG}" 2>&1 \
  && echo "ok $(date -u +%FT%TZ)" > "${DONE}" \
  || echo "fail $(date -u +%FT%TZ)" > "${FAILED}"

rm -f "${RUNNING}"

# ── Remove dangling images to free disk space ──────────────────────────────────
docker image prune -f >> "${LOG}" 2>&1 || true

# ── In debug mode: show outcome and tail container startup logs ────────────────
if [ "${DEBUG}" -eq 1 ]; then
  if [ -f "${DONE}" ]; then
    dbg "Result     : SUCCESS ($(cat "${DONE}"))"
  elif [ -f "${FAILED}" ]; then
    dbg "Result     : FAILED  ($(cat "${FAILED}"))"
  fi
  if [ -n "${RECREATE_AT}" ]; then
    dbg "Container logs since recreate (10 s):"
    # Brief pause so the container has time to emit its first startup lines.
    sleep 2
    timeout 10 docker logs --follow --since "${RECREATE_AT}" "${SERVICE}" 2>&1 || true
  fi
fi

}

main "$@"
