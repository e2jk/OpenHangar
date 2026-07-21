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


# ── Builder stage ─────────────────────────────────────────────────────────────
# All runtime dependencies ship as pre-built musllinux wheels (verified against
# every C-extension dependency at review time — cryptography, Pillow, bcrypt,
# argon2-cffi-bindings, cffi, greenlet, markupsafe, psycopg-binary, SQLAlchemy,
# wrapt), so no compiler or system headers are needed here.
FROM python:3.14-alpine@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92 AS builder

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
# --no-compile: skip .pyc generation (~23 MB). The venv is root-owned and
# read-only to appuser at runtime, so nothing would ever regenerate them —
# each gunicorn worker just re-parses sources once at boot instead.
# --require-hashes fails loudly (not silently falls back to a source build)
# if a musllinux wheel is ever missing for a pinned version/arch — treat
# that as a blocker, not something to fix with an in-image compiler.
RUN python -m venv /venv && \
    /venv/bin/pip install --no-cache-dir --no-compile --require-hashes -r /tmp/pip-bootstrap.txt && \
    /venv/bin/pip install --no-cache-dir --no-compile --require-hashes -r /tmp/requirements.txt

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
# Alpine rootfs (~7 MB) instead of Debian trixie-slim (~81 MB), and
# postgresql18-client is ~4 MB and pulls no Perl — superseding the old
# Debian-specific multi-stage dance (a separate pgclient stage existed
# purely to avoid postgresql-client-18's ~50 MB Perl dependency via
# postgresql-client-common; apk's package has no such dependency, so that
# whole stage is gone).
FROM python:3.14-alpine@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92

ARG VERSION=development
LABEL org.opencontainers.image.version=${VERSION}
LABEL org.opencontainers.image.description="OpenHangar — aircraft ops manager and logbook for owner-operators and clubs, self-hosted and open-source"
ENV OPENHANGAR_VERSION=${VERSION}

ENV PYTHONUNBUFFERED=1
ENV PATH="/venv/bin:$PATH"

# The python:3.14-alpine base image ships its own pip/setuptools/wheel in the
# system site-packages (separate from the app's /venv, which already has
# these stripped in the builder stage above). Nothing in this image ever
# invokes pip at runtime, so drop it here too — same reasoning as the venv
# cleanup: this is what actually clears pip CVE alerts, not just hides them.
# ensurepip/ bundles pip's own wheel (pointless — pip is stripped above) and
# pydoc_data/ backs the `pydoc` CLI/`help()` builtin, neither used here.
# Note: these all originate in the base image's own layers, so deleting them
# here doesn't reduce the exported image size (a later layer's whiteout
# doesn't reclaim bytes an earlier layer already wrote); it's still worth
# doing to keep unused, potentially CVE-flagged files (a stale bundled pip
# wheel) out of scans. Unlike the old Debian stage, no dpkg doc/man
# path-exclude config is needed here — apk installs no man/doc pages by
# default, so there's nothing to exclude.
RUN rm -rf /usr/local/lib/python*/site-packages/pip \
           /usr/local/lib/python*/site-packages/pip-*.dist-info \
           /usr/local/lib/python*/site-packages/setuptools \
           /usr/local/lib/python*/site-packages/setuptools-*.dist-info \
           /usr/local/lib/python*/site-packages/wheel \
           /usr/local/lib/python*/site-packages/wheel-*.dist-info \
           /usr/local/bin/pip3 /usr/local/bin/pip3.* /usr/local/bin/pip \
           /usr/local/lib/python*/ensurepip \
           /usr/local/lib/python*/pydoc_data

# postgresql18-client: pg_dump (backups) + psql (restores), matching the
# postgres:18-alpine server image used elsewhere in this compose stack.
# libpq: shared library those two binaries need (postgresql18-client already
#   depends on it, but pinning it explicitly documents the requirement and
#   survives a future postgresql18-client dependency change).
# tzdata: Alpine's musl libc ships no IANA timezone database by default —
#   Python's zoneinfo module (and any timezone-aware formatting) needs it.
# bash: docker-entrypoint.sh below is a bash script (restore.sh/upgrade.sh
#   also use #!/usr/bin/env bash, but both run on the Docker *host*, not in
#   this container, so only entrypoint's shebang matters here) — adding the
#   ~5 MB package is far lower-risk than rewriting it POSIX-sh-clean.
# apk upgrade first: pulls in security patches not yet in the pinned base
#   image digest, same purpose as the old image's `apt-get upgrade -y`.
# hadolint ignore=DL3018
RUN apk upgrade --no-cache && \
    apk add --no-cache postgresql18-client libpq tzdata bash

WORKDIR /app

# Create non-root user early so --chown on COPY instructions below is valid.
# This avoids a slow recursive chown over /app after the fact. BusyBox's
# adduser (no useradd on Alpine) picks the same uid/gid 1000 as the old
# Debian useradd did for the first non-system user — bind-mount ownership
# instructions elsewhere (docs/development.md) rely on that being 1000.
RUN adduser -D -s /bin/bash appuser

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

# Copy entrypoint (executable) and helper script (invoked as `python
# wait-for-postgres.py`, so it needs no execute bit of its own)
COPY --chown=appuser:appuser --chmod=755 docker/docker-entrypoint.sh /usr/local/bin/
COPY --chown=appuser:appuser docker/wait-for-postgres.py /usr/local/bin/

RUN mkdir -p /data/uploads /data/backups && chown -R appuser:appuser /data

USER appuser

EXPOSE 5000

# Readiness probe: /health/ready verifies DB connectivity and is restricted to
# in-container (loopback) callers. start-period covers app boot + migrations.
# python's urlopen raises on any non-2xx response, so no curl is needed at runtime.
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5000/health/ready', timeout=4)"]

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
