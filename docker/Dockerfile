# ── Vendor assets stage ────────────────────────────────────────────────────────
# Runs npm ci against the lock file (integrity-verified by npm), then uses
# scripts/install_vendor_assets.py to copy the needed files into /tmp/vendor/.
# No node, npm, or node_modules survive into the runtime image.
# Layer order: npm ci is cached independently of script changes.
FROM node:24-alpine@sha256:a0b9bf06e4e6193cf7a0f58816cc935ff8c2a908f81e6f1a95432d679c54fbfd AS vendor
WORKDIR /tmp/vendor-build
COPY requirements/package.json requirements/package-lock.json ./
# hadolint ignore=DL3018
RUN apk add --no-cache python3 && npm ci --ignore-scripts
COPY scripts/install_vendor_assets.py ./
RUN python3 install_vendor_assets.py \
        --copy-only \
        --requirements-dir /tmp/vendor-build \
        --output-dir /tmp/vendor


# ── PostgreSQL client stage ───────────────────────────────────────────────────
# postgresql-client-18 depends on postgresql-client-common, which depends on
# perl (~50 MB) purely for its wrapper scripts (pg_wrapper, update-alternatives
# management) — none of which the runtime image uses. Build the full package
# here, in an isolated stage, and copy out only the two binaries actually
# used (pg_dump for backups, psql for restores). The runtime stage below
# installs libpq5 instead of postgresql-client-18 for the shared libraries
# these binaries need — libpq5 has no perl dependency.
FROM python:3.14-slim@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1 AS pgclient
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /tmp/pgdg.asc \
    && gpg --dearmor < /tmp/pgdg.asc > /usr/share/keyrings/postgresql.gpg \
    && rm /tmp/pgdg.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && apt-get purge -y --auto-remove curl gnupg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*


# ── Builder stage ─────────────────────────────────────────────────────────────
# All runtime dependencies ship as pre-built binary wheels, so no compiler or
# system headers are needed here.
FROM python:3.14-slim@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1 AS builder

COPY requirements/pip-bootstrap.txt /tmp/pip-bootstrap.txt
COPY requirements/runtime.txt /tmp/requirements.txt

# Upgrade pip before it's used to install anything else: the base image
# ships pip 26.1.1, vulnerable to a console_scripts/gui_scripts path-traversal
# issue (fixed in 26.1.2) that's exploitable during package installation
# itself. Stripping pip from the final image (below) doesn't help with that —
# it's this install step that needs to run on a patched version.
# Hash-pinned via requirements/pip-bootstrap.txt (not an inline version,
# floor or otherwise) so this satisfies OpenSSF Scorecard's
# Pinned-Dependencies check *and* stays current: that file matches Renovate's
# "requirements/*.txt" pattern, so Renovate bumps it automatically under the
# same 14-day minimumReleaseAge + automerge policy as every other dependency
# here — no need to hand-edit a version string buried in this Dockerfile.
RUN python -m venv /venv && \
    /venv/bin/pip install --no-cache-dir --require-hashes -r /tmp/pip-bootstrap.txt && \
    /venv/bin/pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt

# Babel ships CLDR locale data for every locale on Earth (~30 MB); the app
# serves en, fr, nl. Keep root.dat + those locales (including regional
# variants such as fr_BE/nl_BE that a browser may send).
RUN find /venv/lib/python*/site-packages/babel/locale-data/ -name '*.dat' \
        ! -name 'root.dat' ! -name 'en.dat' ! -name 'en_*.dat' \
        ! -name 'fr.dat' ! -name 'fr_*.dat' \
        ! -name 'nl.dat' ! -name 'nl_*.dat' \
        -delete

# The runtime never installs packages — drop pip (and friends) from the venv.
# `pip uninstall` alone can leave the *-dist-info/METADATA behind (which is
# all vulnerability scanners key off), so also remove the package/dist-info
# directories explicitly — this is what actually clears CVE alerts against
# a pip version we don't ship.
RUN /venv/bin/pip uninstall -y pip setuptools wheel && \
    rm -rf /venv/lib/python*/site-packages/pip \
           /venv/lib/python*/site-packages/pip-*.dist-info \
           /venv/lib/python*/site-packages/setuptools \
           /venv/lib/python*/site-packages/setuptools-*.dist-info \
           /venv/lib/python*/site-packages/wheel \
           /venv/lib/python*/site-packages/wheel-*.dist-info


# ── Runtime stage ─────────────────────────────────────────────────────────────
# Lean image: only runtime OS packages + the pre-built venv from the builder.
FROM python:3.14-slim@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1

ARG VERSION=development
LABEL org.opencontainers.image.version=${VERSION}
LABEL org.opencontainers.image.description="OpenHangar — aircraft ops manager and logbook for owner-operators and clubs, self-hosted and open-source"
ENV OPENHANGAR_VERSION=${VERSION}

ENV PYTHONUNBUFFERED=1
ENV PATH="/venv/bin:$PATH"

# The python:3.14-slim base image ships its own pip/setuptools/wheel in the
# system site-packages (separate from the app's /venv, which already has
# these stripped in the builder stage above). Nothing in this image ever
# invokes pip at runtime, so drop it here too — same reasoning as the venv
# cleanup: this is what actually clears pip CVE alerts, not just hides them.
RUN rm -rf /usr/local/lib/python*/site-packages/pip \
           /usr/local/lib/python*/site-packages/pip-*.dist-info \
           /usr/local/lib/python*/site-packages/setuptools \
           /usr/local/lib/python*/site-packages/setuptools-*.dist-info \
           /usr/local/lib/python*/site-packages/wheel \
           /usr/local/lib/python*/site-packages/wheel-*.dist-info \
           /usr/local/bin/pip3 /usr/local/bin/pip3.* /usr/local/bin/pip

# curl + gnupg: PostgreSQL apt key download/import only — both purged once the
#   repo is configured (the HEALTHCHECK uses a Python urllib probe instead)
# libpq5: shared library pg_dump/psql (copied in from the pgclient stage
#   below) link against — official PostgreSQL apt repo required because
#   Debian's own libpq5 is built for pg_dump/psql 17, not the 18 binaries
#   used here (server is postgres:18). Unlike postgresql-client-18, libpq5
#   pulls in no perl dependency.
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
    && apt-get install -y --no-install-recommends libpq5 \
    && apt-get purge -y --auto-remove curl gnupg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# pg_dump/psql binaries only (see pgclient stage above) — not the whole
# postgresql-client-18 package, which pulls in perl via
# postgresql-client-common for wrapper scripts this image never uses.
COPY --from=pgclient /usr/lib/postgresql/18/bin/pg_dump /usr/lib/postgresql/18/bin/psql \
     /usr/lib/postgresql/18/bin/
ENV PATH="/usr/lib/postgresql/18/bin:$PATH"

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

# Bundle the upgrade script so the entrypoint can publish it to the host volume
COPY --chown=appuser:appuser docker/upgrade.sh /usr/local/bin/upgrade.sh

# Copy entrypoint and helper scripts
COPY --chown=appuser:appuser docker/docker-entrypoint.sh docker/wait-for-postgres.py /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p /data/uploads /data/backups && chown -R appuser:appuser /data

USER appuser

EXPOSE 5000

# Readiness probe: /health/ready verifies DB connectivity and is restricted to
# in-container (loopback) callers. start-period covers app boot + migrations.
# python's urlopen raises on any non-2xx response, so no curl is needed at runtime.
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5000/health/ready', timeout=4)"]

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
