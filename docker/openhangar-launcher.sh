#!/usr/bin/env bash
# docker/openhangar-launcher.sh — run OpenHangar's host scripts from the signed image.
#
# Usage (on the Docker host, from the directory holding .env and
# docker-compose.yml, or anywhere below it):
#   openhangar-launcher.sh upgrade              # cron, every minute (one-click upgrades)
#   openhangar-launcher.sh restore <archive> …  # restore a backup (see docker/restore.sh)
#   openhangar-launcher.sh refresh              # demo instance only (see demo/refresh.sh)
#
# Install it once, outside every folder the container can write to (not in
# ./openhangar/data/…), e.g. next to docker-compose.yml:
#   docker run --rm --entrypoint cat ghcr.io/e2jk/openhangar:latest \
#     /usr/local/bin/openhangar-launcher.sh > /opt/openhangar/openhangar-launcher.sh
#   chmod 755 /opt/openhangar/openhangar-launcher.sh
# (docs/self-hosting.md#one-click-upgrades also verifies the image first.)
#
# Why: the host runs these scripts with Docker rights, so they must never come
# from a folder the container can write to — code running in the container
# could otherwise replace them and take over the host. The launcher never runs
# a file the container wrote. On each run it pulls the image, verifies its
# signature with cosign against this repository's CI (refusing to go on without
# a valid one), copies the requested script out of that verified image by
# digest into a private temporary folder, and runs it from there. Fixes to the
# scripts still reach every install with each release; only this small
# launcher is installed by hand, and it rarely changes.
#
# The container's only input is the upgrade trigger file: it can at most ask
# for an upgrade to the image named in .env (or the one already running),
# which must be signed.
#
set -euo pipefail

main() {

COMMAND="${1:-}"
[ $# -gt 0 ] && shift
case "${COMMAND}" in
  upgrade) SCRIPT_IN_IMAGE=/usr/local/bin/upgrade.sh ;;
  restore) SCRIPT_IN_IMAGE=/usr/local/bin/restore.sh ;;
  refresh) SCRIPT_IN_IMAGE=/app/demo-scripts/refresh.sh ;;
  *)
    echo "Usage: $0 upgrade | restore <archive> [options] | refresh" >&2
    exit 2
    ;;
esac

LAUNCHER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] launcher: $*"; }

# The directory holding .env: the launcher's own, or the nearest parent.
COMPOSE_DIR="${LAUNCHER_DIR}"
while [ "${COMPOSE_DIR}" != "/" ] && [ ! -f "${COMPOSE_DIR}/.env" ]; do
  COMPOSE_DIR="$(dirname "${COMPOSE_DIR}")"
done
[ -f "${COMPOSE_DIR}/.env" ] || { log "ERROR: .env not found above ${LAUNCHER_DIR}"; exit 1; }
ENV_FILE="${COMPOSE_DIR}/.env"
_env_val() { grep -E "^${1}=" "${ENV_FILE}" 2>/dev/null | cut -d= -f2 | tr -d "\"'" | head -1 || true; }

if [ "${COMMAND}" = "upgrade" ]; then
  # Cheap check first: nothing to do (most minutes) means no pull at all.
  # The upgrade bind-mount's host folder: OPENHANGAR_UPGRADE_HOST_DIR in
  # .env, else ./upgrade or ./openhangar/data/upgrade next to .env.
  UPGRADE_DIR="$(_env_val OPENHANGAR_UPGRADE_HOST_DIR)"
  if [ -z "${UPGRADE_DIR}" ]; then
    UPGRADE_DIR="${COMPOSE_DIR}/openhangar/data/upgrade"
    [ -d "${COMPOSE_DIR}/upgrade" ] && UPGRADE_DIR="${COMPOSE_DIR}/upgrade"
  fi
  [ -d "${UPGRADE_DIR}" ] || { log "ERROR: upgrade folder ${UPGRADE_DIR} not found (set OPENHANGAR_UPGRADE_HOST_DIR in .env)"; exit 1; }
  # Tell the app a current launcher watches this folder (config page notice).
  # A new file renamed into place: never written through a link the
  # container may have left at that name.
  marker="$(mktemp "${UPGRADE_DIR}/.launcher.XXXXXX")"
  date -u +%Y-%m-%dT%H:%M:%SZ > "${marker}"
  mv -fT "${marker}" "${UPGRADE_DIR}/launcher.seen" || rm -f "${marker}"
  [ -f "${UPGRADE_DIR}/trigger" ] || exit 0
  export OPENHANGAR_UPGRADE_HOST_DIR="${UPGRADE_DIR}"
fi

IMAGE="$(_env_val OPENHANGAR_IMAGE)"
[ -n "${IMAGE}" ] || IMAGE="ghcr.io/e2jk/openhangar:latest"

# cron's PATH usually lacks /usr/local/bin, where the docs install cosign.
COSIGN="$(command -v cosign || true)"
[ -n "${COSIGN}" ] || { [ -x /usr/local/bin/cosign ] && COSIGN=/usr/local/bin/cosign; }
if [ -z "${COSIGN}" ]; then
  log "ERROR: cosign is not installed: the image cannot be verified, so nothing is run."
  log "Install it: https://docs.sigstore.dev/cosign/system_config/installation/"
  exit 1
fi

log "Pulling ${IMAGE}..."
docker pull --quiet "${IMAGE}" >/dev/null
DIGEST_REF="$(docker image inspect "${IMAGE}" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"
[ -n "${DIGEST_REF}" ] || { log "ERROR: no registry digest for ${IMAGE}: cannot verify it."; exit 1; }
# The same identity docker/upgrade.sh checks: this repository's ci.yml only.
if ! "${COSIGN}" verify \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    --certificate-identity-regexp '^https://github\.com/e2jk/OpenHangar/\.github/workflows/ci\.yml@refs/(pull/[0-9]+/merge|tags/v.*|heads/main)$' \
    "${DIGEST_REF}" >/dev/null 2>&1; then
  log "ERROR: ${DIGEST_REF} is not signed by this repository's CI: nothing is run."
  exit 1
fi
log "Verified ${DIGEST_REF}."

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
docker run --rm --entrypoint cat "${DIGEST_REF}" "${SCRIPT_IN_IMAGE}" > "${WORK}/script.sh"
[ -s "${WORK}/script.sh" ] || { log "ERROR: ${SCRIPT_IN_IMAGE} not found in ${DIGEST_REF}."; exit 1; }

# The scripts find .env from here, not from the temporary folder they run in.
export OPENHANGAR_SCRIPT_DIR="${COMPOSE_DIR}"
export OPENHANGAR_VERIFIED_IMAGE="${DIGEST_REF}"
bash "${WORK}/script.sh" "$@"

}

main "$@"
