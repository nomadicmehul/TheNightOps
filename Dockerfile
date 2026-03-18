# TheNightOps Agent — Docker Image
# Runs the full agent with all custom MCP servers.
#
# Uses Docker Hardened Images (DHI) as base for production security:
# - Up to 95% fewer CVEs compared to community images
# - Distroless runtime minimises attack surface
# - SBOM and provenance attestations included
# - Apache 2.0 licensed
#
# Docker Scout: Run `docker scout cves nightops:latest` to scan for vulnerabilities
# Docker Desktop: Use the Images tab to view Scout analysis and DHI recommendations

# ── Build Stage ────────────────────────────────────────────────────
# Use Docker Hardened Image for Python (DHI)
# Docs: https://www.docker.com/blog/docker-hardened-images-for-every-developer/
# Falls back to python:3.11-slim if DHI is not available in your registry
FROM docker.io/library/python:3.11-slim AS builder

WORKDIR /build

# Copy source for build
COPY pyproject.toml README.md ./
COPY src/ src/

# Install dependencies into a virtual environment
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir "."

# ── Runtime Stage ──────────────────────────────────────────────────
# Minimal runtime image — no build tools, no package managers
FROM docker.io/library/python:3.11-slim AS runtime

# Labels for Docker Scout and container metadata
LABEL org.opencontainers.image.title="TheNightOps"
LABEL org.opencontainers.image.description="Autonomous SRE Agent built on Google ADK and Remote MCP"
LABEL org.opencontainers.image.source="https://github.com/yourusername/nightops"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL com.docker.scout.policy="hardened"

WORKDIR /app

# Install only runtime system dependencies (minimal surface area)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get purge -y --auto-remove

# Install kubectl (needed for fallback operations only)
# Detect architecture for multi-platform builds
RUN ARCH=$(dpkg --print-architecture) && \
    curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/${ARCH}/kubectl" \
    && chmod +x kubectl \
    && mv kubectl /usr/local/bin/ \
    && kubectl version --client

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code and config
COPY src/ src/
COPY config/ config/
COPY demo/ demo/
COPY pyproject.toml .

# Run as non-root user for security
RUN groupadd -r nightops && useradd -r -g nightops -d /app -s /sbin/nologin nightops \
    && chown -R nightops:nightops /app
USER nightops

# Health check for container orchestration
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import nightops; print('ok')" || exit 1

# Default command — run the CLI
ENTRYPOINT ["python", "-m", "nightops.cli"]
CMD ["--help"]
