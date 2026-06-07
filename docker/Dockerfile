# ── Vendor assets stage ────────────────────────────────────────────────────────
# Runs npm ci against the lock file (integrity-verified by npm), then uses
# scripts/install_vendor_assets.py to copy the needed files into /tmp/vendor/.
# No node, npm, or node_modules survive into the runtime image.
# Layer order: npm ci is cached independently of script changes.
FROM node:24-alpine@sha256:2bdb65ed1dab192432bc31c95f94155ca5ad7fc1392fb7eb7526ab682fa5bf14 AS vendor
WORKDIR /tmp/vendor-build
COPY requirements/package.json requirements/package-lock.json ./
# hadolint ignore=DL3018
RUN apk add --no-cache python3 && npm ci --ignore-scripts
COPY scripts/install_vendor_assets.py ./
RUN python3 install_vendor_assets.py \
        --copy-only \
        --requirements-dir /tmp/vendor-build \
        --output-dir /tmp/vendor


# ── Builder stage ─────────────────────────────────────────────────────────────
# All runtime dependencies ship as pre-built binary wheels, so no compiler or
# system headers are needed here.
FROM python:3.14-slim@sha256:c845af9399020c7e562969a13689e929074a10fd057acd1b1fad06a2fb068e97 AS builder

COPY requirements/runtime.txt /tmp/requirements.txt

RUN python -m venv /venv && \
    /venv/bin/pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt


# ── Runtime stage ─────────────────────────────────────────────────────────────
# Lean image: only runtime OS packages + the pre-built venv from the builder.
FROM python:3.14-slim@sha256:c845af9399020c7e562969a13689e929074a10fd057acd1b1fad06a2fb068e97

ARG VERSION=development
LABEL org.opencontainers.image.version=${VERSION}
LABEL org.opencontainers.image.description="OpenHangar — aircraft ops manager and logbook for owner-operators and clubs, self-hosted and open-source"
ENV OPENHANGAR_VERSION=${VERSION}

ENV PYTHONUNBUFFERED=1
ENV PATH="/venv/bin:$PATH"

# curl: HEALTHCHECK + PostgreSQL apt key download
# gnupg: key import only — purged after repo is configured
# postgresql-client-18: pg_dump for backups; official PostgreSQL apt repo
#   required because Debian ships only pg_dump 17 (server is postgres:18)
# upgrade: pulls in security patches not yet in the pinned base image digest
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends curl gnupg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /tmp/pgdg.asc \
    && gpg --dearmor < /tmp/pgdg.asc > /usr/share/keyrings/postgresql.gpg \
    && rm /tmp/pgdg.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && apt-get purge -y --auto-remove gnupg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create non-root user early so --chown on COPY instructions below is valid.
# This avoids a slow recursive chown over /app after the fact.
RUN useradd --create-home --shell /bin/bash appuser

# Copy the fully-built venv from the builder
COPY --from=builder /venv /venv

# Copy translations first so pybabel compile is cached independently of other
# source changes — the compiled .mo files survive the full app/ COPY below.
COPY --chown=appuser:appuser app/translations/ /app/translations/
RUN pybabel compile -d /app/translations

# Copy the rest of the application code
COPY --chown=appuser:appuser app/ /app/

# Copy pre-built vendor assets from the vendor stage (npm-installed, no CDN needed)
COPY --chown=appuser:appuser --from=vendor /tmp/vendor/ /app/static/vendor/

# Bundle the demo management scripts so the host can mount them out
# (kept separate from /app/demo/ which is the Python blueprint)
COPY --chown=appuser:appuser demo/ /app/demo-scripts/

# Bundle the restore script outside /app so it survives the app bind-mount
COPY --chown=appuser:appuser docker/restore.sh /usr/local/bin/restore.sh

# Copy entrypoint and helper scripts
COPY --chown=appuser:appuser docker/docker-entrypoint.sh docker/wait-for-postgres.py /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p /data/uploads /data/backups && chown -R appuser:appuser /data

USER appuser

EXPOSE 5000

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
