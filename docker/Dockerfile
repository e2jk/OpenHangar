# ── Builder stage ─────────────────────────────────────────────────────────────
# All runtime dependencies ship as pre-built binary wheels, so no compiler or
# system headers are needed here.
FROM python:3.14-slim@sha256:7a500125bc50693f2214e842a621440a1b1b9cbb2188f74ab045d29ed2ea5856 AS builder

COPY docker/docker-requirements.txt /tmp/requirements.txt

RUN python -m venv /venv && \
    /venv/bin/pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt


# ── Runtime stage ─────────────────────────────────────────────────────────────
# Lean image: only runtime OS packages + the pre-built venv from the builder.
FROM python:3.14-slim@sha256:7a500125bc50693f2214e842a621440a1b1b9cbb2188f74ab045d29ed2ea5856

ARG VERSION=development
LABEL org.opencontainers.image.version=${VERSION}

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

# Copy the fully-built venv from the builder
COPY --from=builder /venv /venv

# Copy application code and compile translation catalogs (.po → .mo)
COPY app/ /app/
RUN pybabel compile -d /app/translations

# Bundle the demo management scripts so the host can mount them out
# (kept separate from /app/demo/ which is the Python blueprint)
COPY demo/ /app/demo-scripts/

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app

# Copy entrypoint and helper scripts
COPY docker/docker-entrypoint.sh docker/wait-for-postgres.py /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh && \
    chown appuser:appuser /usr/local/bin/docker-entrypoint.sh /usr/local/bin/wait-for-postgres.py

USER appuser

EXPOSE 5000

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
