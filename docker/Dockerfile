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
LABEL org.opencontainers.image.description="OpenHangar — self-hosted aircraft and pilot logbook"
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

# Bundle the demo management scripts so the host can mount them out
# (kept separate from /app/demo/ which is the Python blueprint)
COPY --chown=appuser:appuser demo/ /app/demo-scripts/

# Bundle the restore script outside /app so it survives the app bind-mount
COPY --chown=appuser:appuser docker/restore.sh /usr/local/bin/restore.sh

# Copy entrypoint and helper scripts
COPY --chown=appuser:appuser docker/docker-entrypoint.sh docker/wait-for-postgres.py /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER appuser

EXPOSE 5000

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
