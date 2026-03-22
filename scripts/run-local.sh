#!/usr/bin/env bash
# TheNightOps — Quick Start (Dashboard + Agent Watch)
#
# Usage:
#   bash scripts/run-local.sh           # GCP mode (default): official Google Cloud MCP
#   bash scripts/run-local.sh --local   # Local mode: starts custom MCP servers
#
# GCP mode requires:
#   - gcloud auth application-default login
#   - IAM role: roles/mcp.toolUser
#   - GKE API MCP enabled: gcloud beta services mcp enable container.googleapis.com
#
# Local mode requires:
#   - No GCP project needed (runs custom MCP servers on ports 8001/8002)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

LOCAL_MODE=false
if [[ "${1:-}" == "--local" ]]; then
    LOCAL_MODE=true
fi

# ── Virtual environment setup ────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    echo "Installing TheNightOps and dependencies..."
    pip install -e ".[dev]"
    echo ""
    echo "Virtual environment created and dependencies installed."
    echo ""
else
    source .venv/bin/activate
fi

# ── Load environment variables ───────────────────────────────────
if [ -f "config/.env" ]; then
    set -a
    source config/.env
    set +a
else
    echo "WARNING: config/.env not found."
    echo "  Copy config/.env.example to config/.env and fill in your values."
    echo "  Continuing with defaults..."
    echo ""
fi

# ── Cleanup handler — kill background jobs on exit ───────────────
cleanup() {
    echo ""
    echo "Shutting down TheNightOps..."
    pkill -f "nightops.mcp_servers" 2>/dev/null || true
    pkill -f "nightops.dashboard" 2>/dev/null || true
    kill $(jobs -p) 2>/dev/null || true
    echo "Done."
}
trap cleanup EXIT INT TERM

# ── Kill any existing TheNightOps processes ──────────────────────
pkill -f "nightops.mcp_servers" 2>/dev/null || true
pkill -f "nightops.dashboard" 2>/dev/null || true
pkill -f "nightops.cli.*watch" 2>/dev/null || true
for port in 8001 8002 8888 8090; do
    lsof -ti:$port 2>/dev/null | xargs kill -9 2>/dev/null || true
done
sleep 1

# ── Start services ───────────────────────────────────────────────
if [ "$LOCAL_MODE" = true ]; then
    echo "Mode: LOCAL (custom MCP servers on ports 8001/8002)"
    echo ""
    echo "Starting Kubernetes MCP server on port 8002..."
    python3 -m nightops.mcp_servers.kubernetes.server &
    echo "Starting Cloud Logging MCP server on port 8001..."
    python3 -m nightops.mcp_servers.cloud_logging.server &
    sleep 2
else
    echo "Mode: GCP (official Google Cloud MCP servers)"
    echo ""
    echo "Prerequisites:"
    echo "  - gcloud auth application-default login"
    echo "  - IAM role: roles/mcp.toolUser on your GCP project"
    echo "  - GKE MCP enabled: gcloud beta services mcp enable container.googleapis.com"
    echo ""
fi

echo "Starting Dashboard on port 8888..."
python3 -m nightops.dashboard.app &
sleep 2

echo ""
echo "============================================"
echo "  TheNightOps v0.3.0 is ready!"
echo "  Dashboard: http://localhost:8888"
if [ "$LOCAL_MODE" = true ]; then
    echo "  Mode: Local Custom MCP"
    echo "  K8s MCP:  http://localhost:8002"
    echo "  Logs MCP: http://localhost:8001"
else
    echo "  Mode: GCP Official MCP"
    echo "  GKE MCP:  container.googleapis.com/mcp"
    echo "  Logs MCP: logging.googleapis.com/mcp"
fi
echo "============================================"
echo ""
echo "Starting agent watch mode... (Ctrl+C to stop)"
echo ""

# Start agent watch (foreground — Ctrl+C triggers cleanup)
python3 -m nightops.cli agent watch
