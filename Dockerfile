# Multi-stage build for production optimization
FROM python:3.12-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    g++ \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set work directory for builder
WORKDIR /build

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Production stage
FROM python:3.12-slim

# The VECTO model contract pins the exact elettra-core source revision.  Keep
# that identity in an image-owned file: a runtime environment variable alone
# could claim that an image built from another revision is compatible.
ARG ELETTRA_CORE_SOURCE_COMMIT=""
LABEL org.opencontainers.image.revision="${ELETTRA_CORE_SOURCE_COMMIT}"
RUN case "${ELETTRA_CORE_SOURCE_COMMIT}" in \
      ""|[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;; \
      *) echo "ELETTRA_CORE_SOURCE_COMMIT must be empty or an exact lowercase 40-character Git SHA" >&2; exit 2 ;; \
    esac && \
    printf '%s\n' "${ELETTRA_CORE_SOURCE_COMMIT}" > /etc/elettra-core-image-commit

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/home/elettra/.local/bin:$PATH"

# Install runtime dependencies only
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user for security
RUN groupadd -r elettra && useradd -r -g elettra elettra

# Copy Python packages from builder stage
COPY --from=builder --chown=elettra:elettra /root/.local /home/elettra/.local

# Set work directory
WORKDIR /app

# Copy application code
COPY --chown=elettra:elettra . .

# A production deployment may mount its own docker-specific configuration, but
# the immutable image must also be startable when built from a clean Git tree.
# Keep the tracked, non-secret baseline under a distinct source name so an
# operator's ignored config/elettra-config.docker.yaml is never overwritten in
# the checkout.
RUN cp /app/config/elettra-config.image.yaml /app/config/elettra-config.docker.yaml && \
    chown elettra:elettra /app/config/elettra-config.docker.yaml

# Bind the marker to the bytes that are actually shipped.  The Git SHA marker
# alone is caller supplied and cannot prove that the image was built from that
# checkout; model preflight checks both identities.
RUN PYTHONPATH=/home/elettra/.local/lib/python3.12/site-packages:/app \
    python -c "from elettra_core import source_tree_sha256; print(source_tree_sha256())" \
    > /etc/elettra-core-image-tree-sha256

# Create necessary directories
RUN mkdir -p /app/data /app/logs && \
    chown -R elettra:elettra /app

# Switch to non-root user
USER elettra

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
