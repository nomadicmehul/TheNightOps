# THE NightOps 🌙

### *Triage. Heal. Evolve.* — Your Autonomous SRE Agent for the Incidents That Don't Wait Until Morning

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Google ADK](https://img.shields.io/badge/Google-ADK-4285F4.svg)](https://google.github.io/adk-python/)
[![MCP](https://img.shields.io/badge/MCP-Remote-green.svg)](https://modelcontextprotocol.io/)
[![Gemini](https://img.shields.io/badge/Gemini-3.1--Pro--Preview-4285F4.svg)](https://ai.google.dev/)
[![Beta](https://img.shields.io/badge/status-🧪_beta-blueviolet)](https://github.com/nomadicmehul/thenightops/)

---

**THE NightOps** (**T**riage. **H**eal. **E**volve.) is an autonomous SRE agent built on [Google Agent Development Kit (ADK)](https://google.github.io/adk-python/) and powered by **Gemini 3.1 Pro Preview**. It connects to your Kubernetes clusters and Google Cloud infrastructure through [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) servers — including **official Google Cloud MCP** for GKE and Cloud Logging. When an incident fires, it doesn't just notify — it **investigates, correlates, diagnoses, drafts an RCA, and recommends remediation**, all before your on-call engineer finishes reading the alert.

---

## The Problem

Incident response in modern cloud-native environments is broken — not because of bad tooling, but because the human cost of correlating information across a dozen systems at 3 AM is unsustainable. Here are real scenarios that every SRE, DevOps, and platform engineering team has lived through:

### The Monday Morning OOMKill Cascade

A team deploys version 4.0 of their report service on Friday afternoon. Weekend traffic is light — everything looks fine. Dashboards are green. Nobody thinks twice.

Monday morning hits. Traffic surges. Suddenly, every report-service pod starts getting OOMKilled. The new version needs more memory than v3.0, but nobody updated the resource limits. The pods restart, can't serve requests during the restart window, and every downstream microservice that depends on report-service starts throwing 504s. What started as one service's memory problem becomes a platform-wide cascading failure.

The on-call engineer sees **47 alerts across 12 services** — but they all trace back to one bad resource limit on one deployment. It takes 45 minutes to figure that out because each alert looks like its own independent problem.

*Based on real incidents documented across the Kubernetes community — this exact pattern is one of the most common causes of production outages in microservice architectures.*

### The Config Change Nobody Noticed

At 11 PM, a platform engineer merges a "minor" Terraform change that adjusts connection pool sizes across three microservices. The change passes CI, gets auto-applied, and nobody thinks twice.

Two hours later, the database starts rejecting connections — the new pool size is too aggressive, and 200 services are now competing for 500 database connections that used to be shared across 50. The monitoring dashboard shows everything green except one metric buried in a custom Grafana panel nobody checks at 1 AM.

By the time PagerDuty fires, the cascading timeouts have spread to the payment service, and **real transactions are failing**. The on-call engineer spends 30 minutes suspecting a database issue before discovering it was a config change from two hours ago, buried in a Terraform apply log that nobody was watching.

*Configuration-related outages account for a significant share of cloud-native incidents — and they're the hardest to correlate because the cause and the symptom are separated by time and by system.*

### Alert Fatigue Meets Real Incident

Your monitoring fires 200+ alerts per day. Most are noise — brief CPU spikes, transient network blips, pods that auto-heal. Your on-call engineer has learned to glance at alerts and dismiss them.

Then one Tuesday at 3 AM, an alert fires that looks exactly like the usual noise: "Pod restart count elevated in namespace payments." Except this time, it's not auto-healing. The pods are stuck in CrashLoopBackOff because a dependency service silently changed its API contract. By the time someone actually investigates — **40 minutes later** — customer-facing payments have been down for 35 of those minutes.

The post-mortem concludes with "we need better alerting." Six months later, the team has even more alerts, even more fatigue, and the same problem waiting to happen again.

*According to the 2025 SRE Report, 40% of SRE teams handle 1-5 incidents per month, with alert fatigue consistently ranked as a top challenge. More alerts don't solve the problem — intelligence does.*

### The Toil Trap

Your team resolves 5 incidents this week. Each one required: logging into Cloud Console, cross-referencing 3 dashboards, reading 2 runbook pages, manually writing an RCA, and sending a Slack summary to stakeholders.

The incidents took 30 minutes to diagnose. **The paperwork took 90 minutes.** By Friday, your senior SRE has spent more time documenting incidents than preventing them. And the RCA from the 3 AM Wednesday incident? It's three sentences because the engineer was exhausted. The one from Thursday afternoon is two pages. Neither is useful for the next person who sees the same failure.

*The 2025 SRE industry data shows toil rose 6% year-over-year, and 28% of SREs report feeling MORE stressed after incidents are resolved — not because of the incident itself, but because of the post-incident documentation and communication burden.*

### What These Scenarios Have in Common

Every one of these scenarios shares the same structural problem: **the information needed to diagnose and resolve the incident existed in the system — scattered across logs, events, deployment history, and monitoring metrics.** A human had to manually assemble it, under pressure, often sleep-deprived, often alone.

The traditional answer has been more tooling, more dashboards, more alerting rules. But more tooling without intelligence just creates more noise.

**The real answer is an AI-powered SRE agent that can reason across all of this — and act.**

**THE NightOps changes this.** It reduces Mean Time to Resolution from 45+ minutes to under 5 minutes — autonomously.

---

## Architecture

THE NightOps supports **two operating modes** — choose based on your environment:

### Simple Mode (Recommended)

Single-agent architecture using `kubectl` tools directly. Reliable, works with any Kubernetes cluster.

```
┌──────────────────────────────────────────────────────────────┐
│          THE NightOps Simple Agent (ADK + Gemini)              │
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │              Single SRE Investigator Agent                │ │
│  │                                                         │ │
│  │  Phase 1: Triage → Phase 2: Deep Investigation →        │ │
│  │  Phase 3: Synthesis → Phase 4: Remediation              │ │
│  └────────────────────────┬────────────────────────────────┘ │
└───────────────────────────┼──────────────────────────────────┘
                            │ subprocess calls
    ┌───────────────────────┼───────────────────────┐
    │                       │                       │
┌───┴────────┐  ┌──────────┴──────┐  ┌─────────────┴──┐
│  kubectl   │  │  kubectl logs   │  │  kubectl top    │
│  get pods  │  │  kubectl desc   │  │  kubectl events │
│  get deploy│  │  get yaml       │  │  rollout history│
└────────────┘  └─────────────────┘  └────────────────┘
            Any Kubernetes cluster (kubectl configured)
```

**Why Simple Mode?** The official GCP MCP servers are still in preview/beta and can be unreliable. Google ADK's internal `TaskGroup` can stall when MCP connections fail mid-investigation. Simple Mode bypasses all of this while still demonstrating the same ADK agent patterns (tool calling, structured investigation, phase tracking, dashboard integration) — just without the infrastructure fragility.

### Full MCP Mode (Advanced)

Multi-agent architecture using [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) for tool integration. More powerful but requires GCP MCP setup.

```
┌──────────────────────────────────────────────────────────────────┐
│                  THE NightOps Agent (ADK + Gemini)                 │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐    │
│  │   Log    │  │ Deploy   │  │ Runbook  │  │ Communication│    │
│  │ Analyst  │  │Correlator│  │Retriever │  │   Drafter    │    │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬───────┘    │
│       │              │             │                │            │
│  ┌────┴──────────────┴─────────────┴────────────────┴─────────┐  │
│  │            Root Orchestrator + Anomaly Detector              │  │
│  └──────────────────────────┬──────────────────────────────────┘  │
└─────────────────────────────┼─────────────────────────────────────┘
                              │ MCP Protocol
              ┌───────────────┼───────────────┐
              │               │               │
       ┌──────┴──────┐ ┌─────┴──────┐ ┌──────┴──────┐
       │   GKE MCP   │ │   Cloud    │ │   Slack /   │
       │  (official)  │ │Observability│ │Notifications│
       │ container.   │ │  (official) │ │  (optional) │
       │ googleapis   │ │ logging.   │ │             │
       │  .com/mcp   │ │ googleapis │ │ Email       │
       │             │ │  .com/mcp  │ │ Telegram    │
       └─────────────┘ └────────────┘ └─────────────┘
         Official Google Cloud MCP (IAM auth, zero infrastructure)
```

### Comparison

| | Simple Mode (`--simple`) | Full MCP Mode |
|---|---|---|
| **Tools** | `kubectl` subprocess calls | GCP MCP servers (beta) |
| **Agents** | 1 single investigator | 6 (root + 5 sub-agents) |
| **Dependencies** | `kubectl` + kubeconfig | GCP MCP, IAM, beta CLI |
| **Reliability** | High (direct kubectl) | Can stall (MCP/TaskGroup issues) |
| **Setup time** | 0 min (just kubectl) | 15-30 min (IAM, MCP enablement) |
| **ADK patterns** | Single agent + function tools | Multi-agent orchestration + MCP |
| **Dashboard** | Full support | Full support |
| **Remediation** | Investigation + RCA only (read-only) | Investigation + auto-remediation |

### Project Structure

```
src/
  agents/
    simple_agent.py          # Plan B: Single agent + kubectl tools (recommended)
    root_orchestrator.py     # Plan A: Multi-agent orchestrator + MCP
    log_analyst.py           # Plan A sub-agent: Cloud Logging analysis
    deployment_correlator.py # Plan A sub-agent: K8s events, pod health
    runbook_retriever.py     # Plan A sub-agent: Historical patterns
    communication_drafter.py # Plan A sub-agent: RCA generation
    anomaly_detector.py      # Plan A sub-agent: Proactive detection
  core/
    config.py                # Pydantic settings, YAML + .env loader
    models.py                # WebhookAlert, IncidentRecord, etc.
  ingestion/
    webhook_receiver.py      # FastAPI on :8090 (Grafana/Alertmanager/PagerDuty)
    deduplication.py          # Fingerprint-based alert dedup
  intelligence/
    incident_memory.py       # TF-IDF similarity matching for past incidents
  remediation/
    policy_engine.py         # 4-level graduated autonomy
  dashboard/
    app.py                   # FastAPI + WebSocket real-time dashboard (:8888)
  cli.py                     # Typer CLI: nightops agent/dashboard/verify/...

config/
  nightops.yaml              # Agent + MCP server config
  remediation_policies.yaml  # Remediation approval policies
  .env                       # Secrets (gitignored)

scripts/
  setup-gke.sh               # GKE cluster + IAM setup
  demo-gke.sh                # One-command GKE demo
  test-scenarios.sh          # Run all 5 scenarios + verify
  cleanup.sh                 # Tear down resources

demo/
  scenarios/demo_app.py      # FastAPI app with 5 failure modes
  k8s_manifests/             # Demo K8s templates
```

---

## Quick Start (Step-by-Step)

### Step 1: Clean Setup

```bash
# Clone and install
git clone https://github.com/nomadicmehul/thenightops.git
cd thenightops
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Configure
cp config/.env.example config/.env
# Edit config/.env — set GOOGLE_API_KEY and GCP_PROJECT_ID
```

### Step 2: Create GKE Cluster + Deploy Demo App

```bash
# Option A: Full setup (cluster + IAM + demo app)
./scripts/demo-gke.sh

# Option B: Cluster only (if you already have demo workloads)
./scripts/setup-gke.sh
```

### Step 3: Verify Cluster

```bash
kubectl get pods -n nightops-demo
kubectl get events -n nightops-demo
```

### Step 4: Start Dashboard + Agent (2 terminals)

```bash
# Terminal 1 — Dashboard
source .venv/bin/activate && source config/.env
nightops dashboard    # Opens http://localhost:8888

# Terminal 2 — Agent (Simple Mode)
source .venv/bin/activate && source config/.env
nightops agent watch --simple
```

### Step 5: Run All Test Scenarios

```bash
bash scripts/test-scenarios.sh
```

### Step 6: Watch Results

Open `http://localhost:8888` — investigations appear in real-time as each scenario triggers.

### Step 7: Cleanup

```bash
./scripts/cleanup.sh
```

---

## Quick Start (Minimal)

### Prerequisites

- Python 3.11+
- Google Cloud SDK (`gcloud`) with an active project
- A GKE cluster (for live demo)
- Google AI API key (for Gemini)
- Slack bot token (optional)
- Docker (for building images)

### Installation

```bash
# Clone the repository
git clone https://github.com/nomadicmehul/thenightops.git
cd thenightops

# Create virtualenv and install
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Configure
cp config/.env.example config/.env
# Edit config/.env — set GOOGLE_API_KEY and GCP_PROJECT_ID

# Authenticate with GCP
gcloud auth application-default login

# Verify setup
nightops verify
```

### Simple Mode (Recommended First Test)

The simplest and most reliable way to try THE NightOps. Uses `kubectl` subprocess calls instead of MCP — works with **any** Kubernetes cluster, no GCP MCP setup needed.

```bash
# Investigate a specific incident
nightops agent run --simple --incident "Pod crashlooping in default namespace"

# Interactive mode
nightops agent run --simple --interactive

# Autonomous watch mode (webhooks + event watcher + proactive checks)
nightops agent watch --simple
```

> **Why Simple Mode?** The official GCP MCP servers (`container.googleapis.com/mcp`, `logging.googleapis.com/mcp`) are still in preview/beta and can be unreliable (see [Known Limitations](#known-limitations--status)). Google ADK's internal `TaskGroup` can also stall when MCP connections fail mid-investigation. Simple Mode bypasses all of this by talking directly to your cluster via `kubectl`, while still using ADK + Gemini for AI-powered reasoning. It demonstrates the same ADK agent patterns (tool calling, structured investigation, phase tracking) without the infrastructure fragility.

### Run with MCP (Full Multi-Agent Mode)

```bash
# One command to start everything (dashboard + agent with official GCP MCP)
bash scripts/run-local.sh

# Or use local custom MCP servers (no GCP project needed)
bash scripts/run-local.sh --local

# Open http://localhost:8888 for the live investigation dashboard
```

### Deploy to GKE

```bash
# Full setup + demo (creates cluster, builds images, deploys, triggers scenario)
./scripts/demo-gke.sh

# Specific scenario
./scripts/demo-gke.sh --scenario cpu-spike

# Quick redeploy (skip cluster setup and image build)
./scripts/demo-gke.sh --skip-setup --skip-build
```

---

## MCP Servers

THE NightOps supports two MCP modes:

### GCP Mode (Default) — Official Google Cloud MCP

Uses Google's managed remote MCP servers. Zero infrastructure to manage — just IAM authentication.

| Server | Endpoint | What It Does |
|--------|----------|--------------|
| **GKE MCP** | `container.googleapis.com/mcp` | Pods, deployments, events, logs, resources |
| **Cloud Observability MCP** | `logging.googleapis.com/mcp` | Log queries, error patterns, anomalies, traces |

**Setup:**
```bash
gcloud auth application-default login
gcloud beta services mcp enable container.googleapis.com --project=$GCP_PROJECT_ID
```

### Local Mode (`--local`) — Custom MCP Servers

Self-hosted MCP servers with SSE transport. No GCP project needed.

| Server | Port | Tools |
|--------|------|-------|
| **Kubernetes MCP** | 8002 | `get_pod_status`, `get_pod_logs`, `get_events`, `get_deployments`, `describe_pod`, `get_resource_usage` |
| **Cloud Logging MCP** | 8001 | `query_logs`, `detect_error_patterns`, `get_log_volume_anomalies`, `correlate_logs_by_trace` |

### Optional Servers

- **Slack MCP** (port 8004) — Posts incident updates, RCA summaries, stakeholder notifications
- **Notifications MCP** (port 8005) — Email (SMTP), Telegram Bot, WhatsApp Business API

---

## Investigation Dashboard

THE NightOps includes a **real-time investigation dashboard** — a web UI that streams agent activity as it happens, so you (and your conference audience) can watch the investigation unfold live.

```bash
# Launch the dashboard
nightops dashboard              # Opens http://localhost:8888
nightops dashboard --port 9000  # Custom port
```

The dashboard shows:

- **Live Investigation Timeline** — Every agent action streamed in real-time via WebSocket
- **Phase Progress** — Visual indicator tracking Triage → Deep Investigation → Synthesis → Remediation
- **Findings Grid** — Each finding displayed as a card with severity, source agent, and description
- **RCA Summary** — Auto-generated Root Cause Analysis rendered when investigation completes
- **Multi-Investigation Support** — Track multiple concurrent investigations side-by-side

The dashboard is built on FastAPI + WebSocket and requires no external dependencies beyond the core project.

---

---

## Agent Architecture

THE NightOps uses Google ADK's multi-agent orchestration to coordinate parallel investigation with 5 specialist sub-agents:

- **Root Orchestrator** — Receives incidents, coordinates investigation, synthesises findings, and recommends remediation
- **Log Analyst** — Cloud Logging pattern analysis, anomaly detection, and trace correlation
- **Deployment Correlator** — Kubernetes deployments, pod health, events, and resource exhaustion
- **Runbook Retriever** — Historical incident patterns, event timelines, and past resolutions
- **Communication Drafter** — Generates structured RCA reports and stakeholder updates
- **Anomaly Detector** — Proactive monitoring: CrashLoopBackOff, OOMKill, memory trends, error rates

### Investigation Flow

**Simple Mode (Plan B):**
```
Incident Received (webhook / dashboard / CLI)
      │
      ▼
Single Agent (ADK + Gemini) runs kubectl tools
      │
      ├── Phase 1: Triage (pods, events, namespaces)
      ├── Phase 2: Deep Investigation (logs, describe, resource YAML)
      ├── Phase 3: Synthesis (correlate findings)
      └── Phase 4: RCA + Recommendations
      │
      ▼
Output: Root Cause Analysis (read-only, no auto-remediation)
Human takes recommended actions manually
```

**Full MCP Mode:**
```
Incident Received (webhook / K8s event / proactive detection)
      │
      ├──→ Log Analyst (parallel)         ──→ Error patterns, anomalies, traces
      ├──→ Deployment Correlator (parallel) ──→ Recent changes, pod health, events
      └──→ Runbook Retriever (parallel)    ──→ Historical patterns, past resolutions
      │
      ▼
Root Orchestrator synthesises findings
      │
      ├── Root Cause Analysis with confidence level
      ├── Immediate remediation actions (auto-approve vs needs approval)
      └── Long-term recommendations
      │
      ▼
Remediation Policy Engine checks action level:
  Level 0 (Auto-approve): Silence alert, create incident, post Slack
  Level 1 (Env-gated):    Auto in dev/staging, human approval in production
  Level 2 (Always ask):   Rollback, scale down, revert config
  Level 3 (Blocked):      Delete namespace, drain node — never allowed
```

---

## Demo Scenarios

Each scenario simulates a real-world incident pattern. Deploy to GKE, trigger the scenario, and watch THE NightOps investigate autonomously.

### Scenario 1: Memory Leak → OOMKill Cascade

*Inspired by the Monday Morning OOMKill pattern described above.*

| | |
|---|---|
| **What happens** | A deployment introduces a memory leak. Pods gradually consume memory until they hit the 256Mi limit and are OOMKilled. Pods restart but immediately begin leaking again, creating CrashLoopBackOff. Downstream services start failing. |
| **What THE NightOps does** | Detects OOMKilled events via Kubernetes MCP → correlates with recent deployment version change → identifies memory trending upward → finds restart count increasing → drafts RCA pointing to the new image version → recommends rollback |
| **Trigger** | `nightops demo trigger -s memory-leak` |

### Scenario 2: CPU Spike from Bad Query

*Inspired by unoptimised endpoint incidents that affect entire service latency.*

| | |
|---|---|
| **What happens** | A specific API endpoint (`/api/reports/generate`) consumes excessive CPU due to an unoptimised computation. CPU throttling causes high latency on ALL endpoints, not just the problematic one. Timeouts cascade. |
| **What THE NightOps does** | Identifies CPU at limits via Kubernetes MCP → finds error logs pointing to specific endpoint via Cloud Logging MCP → correlates with recent deployment change → recommends disabling the endpoint and increasing CPU limits |
| **Trigger** | `nightops demo trigger -s cpu-spike` |

### Scenario 3: Database Connection Pool Exhaustion

*Inspired by the Config Change Nobody Noticed pattern described above.*

| | |
|---|---|
| **What happens** | Database connection pool is exhausted. The `/api/users` endpoint hangs waiting for a connection, returning 504s. Services depending on user data start failing — the classic cascading failure. |
| **What THE NightOps does** | Maps the failure cascade across services → identifies database timeout as root → traces back to connection pool config change → recommends pod restart to reset pool + circuit breaker implementation |
| **Trigger** | `nightops demo trigger -s cascading-failure` |

### Scenario 4: Config Drift Causing 5xx Errors

*Inspired by the Alert Fatigue scenario — an incident hidden in noise.*

| | |
|---|---|
| **What happens** | A misconfigured environment variable causes 50% of requests to `/api/data` to fail with 500 errors. The error rate looks like "normal noise" for 15 minutes before crossing the alert threshold. |
| **What THE NightOps does** | Detects error rate spike via Cloud Logging MCP → correlates timing with recent environment variable change via Kubernetes MCP → identifies exact config key that changed → recommends reverting the config |
| **Trigger** | `nightops demo trigger -s config-drift` |

### Scenario 5: Aggressive OOMKill

*The extreme case — when memory allocation is deliberate or catastrophically fast.*

| | |
|---|---|
| **What happens** | Aggressive memory allocation consumes available memory within seconds. Kubernetes OOMKills the pod immediately. Pod restarts, allocates again, OOMKilled again — instant CrashLoopBackOff. Unlike the slow memory leak, this one is visible to everyone within 30 seconds. |
| **What THE NightOps does** | Instantly detects OOMKill event via Kubernetes MCP → checks memory limits vs actual usage → identifies the allocation pattern in logs → correlates with deployment change → recommends immediate rollback with higher memory limits |
| **Trigger** | `nightops demo trigger -s oom-kill` |

---

## Configuration

### Switching Gemini Models

Change one line in `config/nightops.yaml`:

```yaml
model: gemini-3.1-pro-preview   # Latest (preview)
model: gemini-2.5-pro           # Stable/GA
model: gemini-2.5-flash         # Fast + cheap
```

Available models (verified via API): `gemini-3.1-pro-preview`, `gemini-3-pro-preview`, `gemini-3-flash-preview`, `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.0-flash`

### Full Config

```yaml
# config/nightops.yaml
agent:
  model: gemini-3.1-pro-preview   # or gemini-2.5-pro for stable
  max_investigation_time: 300
  require_human_approval:
    - pod_restart
    - deployment_rollback

# GCP MODE (default): Official Google Cloud MCP
mcp_servers:
  cloud_observability:
    type: official
    endpoint: https://logging.googleapis.com/mcp
    auth: iam
    enabled: true          # Disable for LOCAL mode

  gke:
    type: official
    endpoint: https://container.googleapis.com/mcp
    auth: iam
    enabled: true          # Disable for LOCAL mode

  # LOCAL MODE: Custom MCP servers (enable these, disable official above)
  kubernetes:
    type: custom
    host: localhost
    port: 8002
    enabled: false         # Enable for LOCAL mode

  cloud_logging:
    type: custom
    host: localhost
    port: 8001
    enabled: false         # Enable for LOCAL mode
```

---

## Key Metrics

| Metric | Before THE NightOps | After THE NightOps |
|--------|-------------------|-------------------|
| MTTR (investigation phase) | 45+ minutes | < 5 minutes |
| RCA consistency | Varies by engineer/time of day | Standardised every time |
| Context assembly time | 20+ minutes of manual correlation | Seconds (parallel agent queries) |
| Stakeholder notification | Manual, delayed, inconsistent | Automatic, immediate, formatted |
| On-call cognitive load | High (raw alerts + 5 dashboards) | Low (pre-diagnosed summary) |
| Post-incident toil | 90+ minutes per incident | < 10 minutes (review + approve) |

---

### Switching Modes

No config changes needed — just a CLI flag:

```bash
# Simple Mode (Plan B) — kubectl, no MCP, read-only
nightops agent watch --simple
nightops agent run --simple --incident "..."

# Full MCP Mode — multi-agent, GKE MCP + Cloud Logging MCP
nightops agent watch
nightops agent run --incident "..."
```

---

## CLI Reference

```bash
# Quick Start
bash scripts/run-local.sh                  # GCP mode (official MCP)
bash scripts/run-local.sh --local          # Local mode (custom MCP servers)

# Setup & Configuration
nightops init                              # Interactive setup wizard
nightops verify                            # Check all dependencies and configuration

# MCP Servers
nightops mcp start --all                   # Start all enabled MCP servers
nightops mcp start kubernetes             # Start specific server
nightops mcp status                        # Check all MCP server status

# Dashboard
nightops dashboard                         # Launch real-time investigation dashboard
nightops dashboard --port 9000             # Custom port

# Agent — Simple Mode (recommended, no MCP dependency)
nightops agent run --simple --incident "pod OOMKilled"  # Direct investigation
nightops agent run --simple --interactive  # Interactive mode
nightops agent watch --simple              # Autonomous watch mode (kubectl tools)

# Agent — Full MCP Mode (requires GCP MCP setup)
nightops agent watch                       # Autonomous watch mode (production)
nightops agent run --interactive           # Interactive investigation mode
nightops agent run --incident "pod OOMKilled"  # Direct investigation

# Test All Scenarios
bash scripts/test-scenarios.sh             # Run all 5 investigations + verify
bash scripts/test-scenarios.sh --scenario 1  # Run specific scenario (1-5)
bash scripts/test-scenarios.sh --verify-only # Just check cluster status

# GKE Demo (Full MCP Mode)
./scripts/demo-gke.sh                     # Full GKE demo (setup + build + deploy)
./scripts/demo-gke.sh --scenario cpu-spike # Specific scenario
./scripts/demo-gke.sh --skip-setup --skip-build  # Quick redeploy
./scripts/cleanup.sh                       # Teardown resources
```

## Development

```bash
# Run tests
pytest tests/ -v

# Run linting
ruff check src/

# Run all services via Docker Compose
docker compose up -d

# Scan Docker images for vulnerabilities
./scripts/docker-scout-scan.sh --fix
```

---

## Roadmap

- [x] Custom Kubernetes MCP server (pod status, events, deployments, logs, resource usage)
- [x] Custom Cloud Logging MCP server (log queries, error patterns, anomaly detection)
- [x] Multi-agent investigation with Google ADK + Gemini 3.1 Pro Preview
- [x] 5 demo scenarios with GKE simulation
- [x] Proactive anomaly detection (CrashLoopBackOff, OOMKill, memory trends)
- [x] TF-IDF incident memory for historical pattern matching
- [x] Graduated remediation (auto-approve → env-gated → always-approve → blocked)
- [x] Real-time investigation dashboard (WebSocket streaming)
- [x] One-command GKE demo (`demo-gke.sh`)
- [x] Slack MCP server (optional)
- [x] Notifications MCP server — Email, Telegram, WhatsApp (optional)
- [x] Google Cloud official MCP server integration (GKE + Cloud Observability)
- [ ] Grafana MCP integration (mcp-grafana)
- [ ] Jira MCP server for ticket creation
- [ ] Auto-remediation with approval workflows
- [ ] Multi-cluster support
- [ ] Learning from past incidents to improve diagnosis accuracy

---

## Known Limitations & Status

> **This project is a working proof-of-concept / reference architecture for Google ADK multi-agent systems.** The demo may not work end-to-end in all environments due to the factors below.

### Google Cloud MCP (Preview)

The official GCP MCP servers (`container.googleapis.com/mcp`, `logging.googleapis.com/mcp`) are in **preview/beta**. This means:

- **MCP enablement requires beta CLI commands** (`gcloud beta services mcp enable ...`) which may not be available in all regions or for all project types.
- **IAM role `roles/mcp.toolUser`** must be granted to both the service account (for GKE deployment) and your user account (for local development). The setup script (`scripts/setup-gke.sh`) handles this automatically, but manual verification may be needed.
- MCP server availability and response times can vary — intermittent connection failures are expected during preview.

### Google ADK Runtime

ADK internally uses Python `TaskGroup` for concurrent MCP tool calls. When an MCP connection fails mid-investigation, this surfaces as an `ExceptionGroup` (Python 3.11+) which can be difficult to handle gracefully. Known impacts:

- **Investigation may stall or fail** if MCP servers are unreachable or return errors. The dashboard will show the investigation stuck at "Triage" or "Deep Investigation" phase.
- **Error recovery is best-effort** — the agent attempts to notify the dashboard of failures, but the original HTTP connection pool may be broken after a `TaskGroup` exception.
- These are ADK framework-level behaviors, not application bugs. They will improve as ADK matures.

### What Works Well

- The **multi-agent architecture** (Root Orchestrator + 5 sub-agents) demonstrates ADK's orchestration capabilities effectively.
- The **real-time dashboard** (WebSocket streaming, phase tracking, timeline, findings) works correctly.
- **Webhook ingestion** (Grafana, Alertmanager, PagerDuty, generic) with deduplication works reliably.
- The **incident memory** (TF-IDF similarity matching) and **remediation policy engine** work as designed.
- The full flow (Triage -> Deep Investigation -> Synthesis -> Remediation -> Completed) works when MCP connections are stable.

### Recommendations for Evaluation

1. **Best first test**: Use Simple Mode — `nightops agent run --simple --incident "your incident"`. Works immediately with any kubectl-accessible cluster.
2. **Run all scenarios**: `bash scripts/test-scenarios.sh` runs 5 incident investigations and verifies cluster state.
3. **If you want full MCP**: Deploy to GKE with `./scripts/demo-gke.sh` — this sets up all IAM/MCP prerequisites automatically.
4. **For ADK learning**: `src/agents/simple_agent.py` shows clean ADK function tool patterns; `src/agents/root_orchestrator.py` shows multi-agent orchestration with MCP.

---

## Contributing

Contributions are welcome! The best ways to contribute:

1. **Add MCP servers** — Each new integration (Jira, PagerDuty, Confluence) expands the agent's reach
2. **Add demo scenarios** — More incident patterns make the agent smarter during demonstrations
3. **Improve agent prompts** — Better reasoning instructions lead to better diagnosis accuracy
4. **Add tests** — Help us maintain reliability across all MCP servers and agents

See [CONTRIBUTING.md](docs/CONTRIBUTING.md) for guidelines.

---

## License

Apache 2.0 — See [LICENSE](LICENSE) for details.

---

## Acknowledgements

- [Google Agent Development Kit (ADK)](https://google.github.io/adk-python/)
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
- [Google Cloud](https://cloud.google.com/) — GKE, Cloud Logging
- The Kubernetes community for documenting real-world failure patterns
- Every SRE who has written a 3 AM post-mortem

---

**Built by engineers who are tired of being tired.**

Developed with ❤️ by **[Mehul Patel](https://nomadicmehul.dev/)** — for the love of DevOps, Cloud Native, and the Open Source community.
