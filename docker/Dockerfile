# Use Python 3.11 slim image for smaller footprint
FROM python:3.11-slim@sha256:9a7765b36773a37061455b332f18e265e7f58f6fea9c419a550d2a8b0e9db834

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    postgresql-client \
    build-essential \
    libpq-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file and install Python dependencies
COPY docker/docker-requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r docker-requirements.txt

# Copy application code
COPY app/ /app/

# Compile translation catalogs (.po → .mo)
RUN pybabel compile -d /app/translations

# Bundle the demo management scripts so the host can mount them out
# (kept separate from /app/demo/ which is the Python blueprint)
COPY demo/ /app/demo-scripts/

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app

# Copy entrypoint script and make it executable
COPY docker/docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh && \
    chown appuser:appuser /usr/local/bin/docker-entrypoint.sh

USER appuser

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Run the application
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
