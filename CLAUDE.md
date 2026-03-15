# Claude Code Instructions

## Project Overview

**TheNightOps** — Autonomous SRE Agent for Kubernetes, built on Google ADK and Gemini.
- **Version:** 0.3.0
- **License:** Apache 2.0
- **Python:** 3.11+
- **Package manager:** pip with hatchling build backend
- **CLI entry point:** `nightops` (mapped to `thenightops.cli:app`)

## Architecture

Multi-agent system using Google ADK for orchestration and MCP (Model Context Protocol) for tool integration.

### Agent Layer (ADK + Gemini)
- **Root Orchestrator** (`src/agents/root_orchestrator.py`) — Coordinates investigation across sub-agents
- **Log Analyst** (`src/agents/log_analyst.py`) — Cloud Logging pattern analysis
- **Deployment Correlator** (`src/agents/deployment_correlator.py`) — K8s events, pod health, recent deployments
- **Runbook Retriever** (`src/agents/runbook_retriever.py`) — Grafana alerts, dashboards, past incidents
- **Communication Drafter** (`src/agents/communication_drafter.py`) — RCA generation, Slack/email/Telegram notifications
- **Anomaly Detector** (`src/agents/anomaly_detector.py`) — Proactive anomaly detection

### MCP Servers
**Official Google Cloud MCP (default for GCP/demo):**
- **GKE MCP** — `https://container.googleapis.com/mcp` (IAM auth via `header_provider`)
- **Cloud Observability MCP** — `https://logging.googleapis.com/mcp` (IAM auth via `header_provider`)

**Custom MCP Servers (for `--local` mode):**
- **Kubernetes** (`src/mcp_servers/kubernetes/server.py`) — Pod status, events, deployments, logs
- **Cloud Logging** (`src/mcp_servers/cloud_logging/server.py`) — GCP log queries
- **Slack** (`src/mcp_servers/slack/server.py`) — Incident notifications (disabled)
- **Notifications** (`src/mcp_servers/notifications/server.py`) — Email/Telegram/WhatsApp (disabled)

### Core Modules
- **Config** (`src/core/config.py`) — Pydantic settings, loads from `config/.env` and `config/nightops.yaml`
- **Models** (`src/core/models.py`) — WebhookAlert, RemediationAction, IncidentRecord, AnomalyCheck, compute_fingerprint
- **Autodiscovery** (`src/core/autodiscovery.py`) — Environment auto-detection
- **CLI** (`src/cli.py`) — Typer-based CLI with commands: agent, verify, metrics, policies, auto-config, demo, dashboard, mcp, init

### Ingestion Pipeline
- **Webhook Receiver** (`src/ingestion/webhook_receiver.py`) — FastAPI app on :8090, accepts Grafana/Alertmanager/PagerDuty/generic webhooks
- **Deduplication** (`src/ingestion/deduplication.py`) — Fingerprint-based dedup with sliding window
- **Event Watcher** (`src/ingestion/event_watcher.py`) — K8s event stream watcher

### Intelligence
- **Incident Memory** (`src/intelligence/incident_memory.py`) — TF-IDF similarity matching for past incidents

### Remediation
- **Policy Engine** (`src/remediation/policy_engine.py`) — 4 levels: auto-approve, env-gated, always-approve, blocked

### Metrics
- **Tracker** (`src/metrics/tracker.py`) — MTTR, impact summaries, incident counts

### Proactive
- **Scheduler** (`src/proactive/scheduler.py`) — Periodic anomaly detection runs

### Dashboard
- **App** (`src/dashboard/app.py`) — FastAPI + WebSocket real-time investigation dashboard on :8888
- **Static** (`src/dashboard/static/nightops.js`) — Frontend JS
- **Templates** (`src/dashboard/templates/index.html`) — Dashboard HTML

## Project Structure

```
src/                          # Main source (packaged as `thenightops`)
  agents/                     # ADK agents (6 agents)
  core/                       # Config, models, logging, autodiscovery
  ingestion/                  # Webhook receiver, dedup, event watcher
  intelligence/               # Incident memory (TF-IDF)
  mcp_servers/                # 4 MCP servers (kubernetes, cloud_logging, slack, notifications)
  remediation/                # Policy engine
  metrics/                    # MTTR/impact tracker
  proactive/                  # Anomaly detection scheduler
  dashboard/                  # Real-time web dashboard
  cli.py                      # CLI entry point
  init_wizard.py              # Interactive setup wizard

config/
  .env.example                # Template — copy to .env
  .env                        # Actual config (gitignored)
  nightops.yaml               # Agent + MCP server config
  remediation_policies.yaml   # Remediation approval policies

deploy/
  gke/                        # K8s manifest templates (committed, placeholder values)
  generated/                  # Rendered manifests (gitignored, real secrets)

demo/
  Dockerfile                  # Demo app container
  scenarios/demo_app.py       # FastAPI app with 5 failure modes
  k8s_manifests/              # Demo K8s templates (DEMO_IMAGE placeholder)

scripts/
  setup-gke.sh                # GKE cluster + IAM + Workload Identity
  generate-manifests.sh       # Renders templates -> deploy/generated/
  demo-gke.sh                 # One-command GKE demo (meetups/workshops)
  demo-local.sh               # Local Docker demo
  demo-live.sh                # Interactive conference demo
  cleanup.sh                  # Tear down resources

tests/                        # pytest tests
```

## Key Patterns

### Manifest Generation
Templates in `deploy/gke/` use placeholders (`AGENT_IMAGE`, `DEMO_IMAGE`). Running `scripts/generate-manifests.sh` reads `config/.env` and renders final manifests into `deploy/generated/` (gitignored). Never edit `deploy/generated/` directly.

### Config Loading
`config/.env` is the single source of truth for secrets and environment-specific values. `config/nightops.yaml` holds agent behavior config. The `src/core/config.py` module loads both using Pydantic settings.

### Default Model
The default Gemini model is `gemini-3.1-pro` (set in `src/core/config.py`).
Supported: `gemini-3.1-pro` (default), `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-3-flash`, `gemini-2.0-flash`

### MCP Modes
- **GCP mode** (default): Official Google Cloud MCP servers (`container.googleapis.com/mcp`, `logging.googleapis.com/mcp`). Requires IAM setup.
- **Local mode** (`--local`): Custom self-hosted MCP servers on ports 8001/8002. No GCP project needed.

## Development Commands

```bash
# Install
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Quick Start (auto-creates venv if missing)
bash scripts/run-local.sh           # GCP mode (official MCP)
bash scripts/run-local.sh --local   # Local mode (custom MCP servers)

# Tests
pytest tests/ -v

# Linting
ruff check src/

# CLI
nightops verify              # Check system readiness
nightops metrics             # Show impact summary
nightops policies            # Show remediation policies
nightops auto-config --dry-run  # Environment discovery
nightops agent watch         # Start webhook receiver (autonomous mode)
nightops agent run --interactive  # Interactive investigation

# Docker
docker compose up -d

# GKE Demo
./scripts/demo-gke.sh                          # Full setup + demo
./scripts/demo-gke.sh --scenario cpu-spike     # Specific scenario
./scripts/demo-gke.sh --skip-setup --skip-build  # Quick redeploy
```

## Demo Scenarios
5 failure scenarios: `memory-leak`, `cpu-spike`, `cascading-failure`, `config-drift`, `oom-kill`

## Webhook Endpoints
- `POST /api/v1/webhooks/grafana` — Grafana alerts
- `POST /api/v1/webhooks/alertmanager` — Prometheus Alertmanager
- `POST /api/v1/webhooks/pagerduty` — PagerDuty
- `POST /api/v1/webhooks/generic` — Custom alerts
- `GET /api/v1/webhooks/health` — Health check

## Git Commits
- Do NOT include `Co-Authored-By` lines in commit messages

## Code Style
- Use `ruff` for linting (target Python 3.11, line length 100)
- Use `mypy` for type checking (strict mode)
- Follow existing patterns when adding new modules
- Prefer editing existing files over creating new ones

---

Developed with ❤️ by **[Mehul Patel](https://nomadicmehul.dev/)** — for the love of DevOps, Cloud Native, and the Open Source community.
