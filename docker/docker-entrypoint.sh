#!/bin/bash
set -e

echo ""
echo "================================"
echo "OpenHangar ${OPENHANGAR_VERSION:-development} — started at $(date '+%Y-%m-%d %H:%M:%S %Z')"

# Function to log the time taken for a specific operation
log_time() {
    local start_time=$1
    local end_time=$2
    local operation_name=$3
    local duration=$((end_time - start_time))
    echo "$operation_name took $duration seconds"
}

# Validate OPENHANGAR_ENV
case "${OPENHANGAR_ENV}" in
  development|test|production|demo) ;;
  *)
    echo "ERROR: OPENHANGAR_ENV must be one of: development, test, production, demo (got: '${OPENHANGAR_ENV}')"
    exit 1
    ;;
esac

# Set default database host if not provided
OPENHANGAR_DB_HOST=${OPENHANGAR_DB_HOST:-db}

# Measure time for waiting for PostgreSQL
start_time_db_wait=$(date +%s)
echo "Waiting for PostgreSQL to be ready at ${OPENHANGAR_DB_HOST}..."
python /usr/local/bin/wait-for-postgres.py
end_time_db_wait=$(date +%s)
log_time $start_time_db_wait $end_time_db_wait "Waiting for PostgreSQL"

echo "PostgreSQL is ready. Initializing database..."

start_time_db_init=$(date +%s)
python docker-init-db.py
end_time_db_init=$(date +%s)
log_time $start_time_db_init $end_time_db_init "Database initialization"

echo "Starting Flask application..."

end_time_app_start=$(date +%s)
log_time $start_time_db_wait $end_time_app_start "Starting the entire web application"

# In demo mode: publish the bundled demo scripts to the host bind-mount so
# the cron job always runs the version shipped with the current image.
if [ "$OPENHANGAR_ENV" = "demo" ] && [ -d "/app/demo-scripts" ] && [ -d "/refresh" ]; then
    echo "Publishing demo scripts to host bind-mount (/refresh)..."
    cp -r /app/demo-scripts/. /refresh/
    chmod +x /refresh/refresh.sh 2>/dev/null || true
fi

# Publish the restore script to the backups bind-mount so the operator can run
# it from the Docker host: /path/to/backups/restore.sh <archive> [--upgrade-to=...]
if [ -f "/usr/local/bin/restore.sh" ] && [ -d "/data/backups" ]; then
    cp /usr/local/bin/restore.sh /data/backups/restore.sh
    chmod +x /data/backups/restore.sh
fi

# Publish the upgrade script to the upgrade bind-mount so the host cron job
# always runs the version shipped with the current image.
if [ -f "/usr/local/bin/upgrade.sh" ] && [ -n "${OPENHANGAR_UPGRADE_DIR:-}" ] && [ -d "${OPENHANGAR_UPGRADE_DIR}" ]; then
    echo "Publishing upgrade script to ${OPENHANGAR_UPGRADE_DIR}/upgrade.sh..."
    cp /usr/local/bin/upgrade.sh "${OPENHANGAR_UPGRADE_DIR}/upgrade.sh"
    chmod +x "${OPENHANGAR_UPGRADE_DIR}/upgrade.sh"
fi

if [ "$OPENHANGAR_ENV" = "development" ]; then
    echo "Running in development mode straight with 'python init.py'"
    python init.py
else
    echo "Running in ${OPENHANGAR_ENV} mode with gunicorn"
    if [ "${OPENHANGAR_ACCESS_LOG:-0}" = "1" ]; then
        ACCESS_LOG_DEST="-"
        echo "HTTP access logging → stdout (OPENHANGAR_ACCESS_LOG=1)"
    elif [ -d /data/logs ]; then
        # /data/logs only exists here if the operator bind-mounted it (it's
        # not created in the image) — under the compose file's
        # read_only: true root filesystem, writing a fresh directory
        # anywhere outside /tmp or a real bind mount would fail outright.
        # Access logs can contain request paths — keep them owner-only (N-25).
        chmod 0700 /data/logs
        ACCESS_LOG_DEST="/data/logs/openhangar-access.log"
        echo "HTTP access logging → ${ACCESS_LOG_DEST}"
    else
        ACCESS_LOG_DEST="-"
        echo "HTTP access logging → stdout (no /data/logs volume mounted; mount ./openhangar/data/logs:/data/logs for a persistent log file instead)"
    fi
    # Worker sizing: OPENHANGAR_WEB_WORKERS processes × OPENHANGAR_WEB_THREADS
    # threads each.  With threads > 1 the gthread worker class is used, so a
    # slow request (GIF/PNG track rendering, GPS parsing, backup ZIP) doesn't
    # block a whole worker.  Lower the worker count on small hosts (e.g. a
    # Raspberry Pi) to reduce the memory footprint.
    WEB_WORKERS="${OPENHANGAR_WEB_WORKERS:-4}"
    WEB_THREADS="${OPENHANGAR_WEB_THREADS:-1}"
    case "${WEB_WORKERS}" in
      ''|*[!0-9]*) echo "WARNING: OPENHANGAR_WEB_WORKERS must be a positive integer (got: '${WEB_WORKERS}') — using 4"; WEB_WORKERS=4 ;;
    esac
    case "${WEB_THREADS}" in
      ''|*[!0-9]*) echo "WARNING: OPENHANGAR_WEB_THREADS must be a positive integer (got: '${WEB_THREADS}') — using 1"; WEB_THREADS=1 ;;
    esac
    if [ "${WEB_THREADS}" -gt 1 ]; then
        WORKER_CLASS="gthread"
    else
        WORKER_CLASS="sync"
    fi
    echo "gunicorn: ${WEB_WORKERS} worker(s) x ${WEB_THREADS} thread(s), worker class ${WORKER_CLASS}"
    # -c gunicorn_conf.py installs RedactingLogger, which masks secret tokens
    # (password-reset / share / invite) in access-log paths (N-25).
    # --worker-tmp-dir /tmp: gunicorn's worker heartbeat file needs a real
    # (non-overlayfs) writable filesystem — under the compose file's
    # read_only: true root filesystem, /tmp is the tmpfs mount that provides
    # that; explicit rather than relying on gunicorn's own default so this
    # keeps working regardless of what TMPDIR resolves to in the image.
    # --no-control-socket: gunicorn 26+ defaults to a control socket under
    # ~/.gunicorn, which the read-only root filesystem can't create — unused
    # here (nothing talks to it), so disabled outright rather than given
    # another writable path to manage.
    gunicorn -c /app/gunicorn_conf.py --bind 0.0.0.0:5000 \
        --workers "${WEB_WORKERS}" --threads "${WEB_THREADS}" \
        --worker-class "${WORKER_CLASS}" --worker-tmp-dir /tmp \
        --no-control-socket \
        --timeout 120 --access-logfile "${ACCESS_LOG_DEST}" wsgi:app
fi
