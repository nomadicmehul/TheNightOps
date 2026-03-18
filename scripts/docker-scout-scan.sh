#!/usr/bin/env bash
# TheNightOps — Docker Scout Security Scan
# Scans all project images for vulnerabilities and recommends hardened alternatives.
#
# Prerequisites:
#   - Docker Desktop with Docker Scout enabled
#   - Docker CLI with scout plugin: docker scout version
#
# Usage:
#   ./scripts/docker-scout-scan.sh                    # Scan all images
#   ./scripts/docker-scout-scan.sh --fix              # Show fix recommendations
#   ./scripts/docker-scout-scan.sh --compare-hardened  # Compare with DHI alternatives

set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-nightops}"
AGENT_IMAGE="nightops:latest"
DEMO_IMAGE="nightops-demo-api:latest"

echo "╔═══════════════════════════════════════════════════╗"
echo "║  TheNightOps — Docker Scout Security Scan         ║"
echo "╚═══════════════════════════════════════════════════╝"
echo ""

# Check if Docker Scout is available
if ! docker scout version &>/dev/null; then
    echo "Error: Docker Scout not found."
    echo "Install via Docker Desktop or: docker plugin install scout"
    exit 1
fi

# ── Build images if needed ─────────────────────────────────────────
echo "→ Building images..."
docker build -t "${AGENT_IMAGE}" -f Dockerfile .
docker build -t "${DEMO_IMAGE}" -f demo/Dockerfile demo/
echo "  ✓ Images built"
echo ""

# ── CVE Scan ───────────────────────────────────────────────────────
echo "═══ CVE Scan: Agent Image ═══"
docker scout cves "${AGENT_IMAGE}" --format summary
echo ""

echo "═══ CVE Scan: Demo Image ═══"
docker scout cves "${DEMO_IMAGE}" --format summary
echo ""

# ── SBOM Generation ───────────────────────────────────────────────
echo "═══ SBOM: Agent Image ═══"
docker scout sbom "${AGENT_IMAGE}" --format list | head -20
echo "  ... (truncated)"
echo ""

# ── Recommendations ───────────────────────────────────────────────
if [[ "${1:-}" == "--fix" ]]; then
    echo "═══ Fix Recommendations: Agent Image ═══"
    docker scout recommendations "${AGENT_IMAGE}"
    echo ""
    echo "═══ Fix Recommendations: Demo Image ═══"
    docker scout recommendations "${DEMO_IMAGE}"
fi

# ── Compare with Docker Hardened Images ───────────────────────────
if [[ "${1:-}" == "--compare-hardened" ]]; then
    echo "═══ Comparing with Docker Hardened Images ═══"
    echo ""
    echo "Current base: python:3.11-slim"
    echo "Recommended:  docker.io/dhi/python:3.11"
    echo ""
    echo "Run the following to compare CVE counts:"
    echo "  docker scout compare ${AGENT_IMAGE} --to docker.io/dhi/python:3.11"
fi

echo ""
echo "╔═══════════════════════════════════════════════════╗"
echo "║  Scan complete! Check results above.              ║"
echo "║                                                   ║"
echo "║  Tips:                                            ║"
echo "║  - Use Docker Desktop → Images tab for visual UI  ║"
echo "║  - Run with --fix for remediation suggestions     ║"
echo "║  - Run with --compare-hardened for DHI comparison ║"
echo "╚═══════════════════════════════════════════════════╝"
