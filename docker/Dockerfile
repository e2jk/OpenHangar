# ── Builder stage ─────────────────────────────────────────────────────────────
# All runtime dependencies ship as pre-built binary wheels, so no compiler or
# system headers are needed here.
FROM python:3.14-slim@sha256:33ef7446e8c14b21cb247e23afbcdc90e98853b70812ca46b2265e769a7dfb8b AS builder

COPY docker/docker-requirements.txt /tmp/requirements.txt

RUN python -m venv /venv && \
    /venv/bin/pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt


# ── Runtime stage ─────────────────────────────────────────────────────────────
# Lean image: only runtime OS packages + the pre-built venv from the builder.
FROM python:3.14-slim@sha256:33ef7446e8c14b21cb247e23afbcdc90e98853b70812ca46b2265e769a7dfb8b

ENV PYTHONUNBUFFERED=1
ENV PATH="/venv/bin:$PATH"

# curl: HEALTHCHECK only; pg readiness is handled by the entrypoint via psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
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
