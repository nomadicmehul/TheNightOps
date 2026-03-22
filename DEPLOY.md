# TheNightOps — Deployment Guide

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         GKE Cluster                             │
│                                                                 │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐        │
│  │  mcp-k8s     │   │ mcp-logging  │   │  mcp-slack   │        │
│  │  :8002       │   │  :8001       │   │  :8004       │        │
│  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘        │
│         │ SSE              │ SSE              │ SSE             │
│  ┌──────┴──────────────────┴──────────────────┴───────┐         │
│  │              nightops-agent                        │         │
│  │  ┌─────────────┐ ┌──────────┐ ┌─────────────────┐ │         │
│  │  │ Webhook     │ │ Event    │ │ Proactive       │ │         │
│  │  │ Receiver    │ │ Watcher  │ │ Scheduler       │ │         │
│  │  │ :8090       │ │          │ │                 │ │         │
│  │  └─────────────┘ └──────────┘ └─────────────────┘ │         │
│  │                                                    │         │
│  │  ┌─────────────────────────────────────────────┐   │         │
│  │  │         ADK Multi-Agent Orchestrator        │   │         │
│  │  │  log_analyst | deploy_correlator | runbook  │   │         │
│  │  │  communication_drafter | anomaly_detector   │   │         │
│  │  └─────────────────────────────────────────────┘   │         │
│  └────────────────────────────────────────────────────┘         │
│                          │                                      │
│  ┌──────────────────┐    │ PVC                                  │
│  │ nightops-dashboard│    │ (incident memory                    │
│  │ :8888            │    │  + metrics)                          │
│  └──────────────────┘    │                                      │
│                          ▼                                      │
│                   nightops-data                                 │
└─────────────────────────────────────────────────────────────────┘
         │                              │
         │ IAM (Workload Identity)      │
         ▼                              ▼
  Google Cloud MCP Servers       Gemini API
  (Cloud Observability, GKE)     (ADK / Agents)
```

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| `gcloud` CLI | Authenticated with project access |
| GKE cluster | Existing or created via `setup-gke.sh` |
| Gemini API key | `GOOGLE_API_KEY` for ADK agents |
| Python 3.11+ | For local dev/testing only |

### Optional
| Service | Purpose |
|---------|---------|
| Slack Bot Token | Incident notifications via Slack |
| Grafana + Service Account Token | Alert history, dashboards, Prometheus queries |
| SMTP credentials | Email notifications |
| Telegram Bot Token | Telegram alerts |

---

## Quick Start (GKE)

```bash
# 1. Configure your environment
cp config/.env.example config/.env
vi config/.env  # fill in GOOGLE_API_KEY, GCP_PROJECT_ID, etc.

# 2. Set up GKE cluster + IAM + Workload Identity
export GCP_PROJECT_ID=your-project-id
./scripts/setup-gke.sh
# → This also auto-generates manifests from your config/.env

# 3. Deploy
kubectl apply -f deploy/generated/

# 4. Verify
kubectl get pods -n nightops
```

---

## How Manifest Generation Works

Templates live in `deploy/gke/` (committed to git with placeholder values).
Running `generate-manifests.sh` reads your `config/.env` and renders
final manifests into `deploy/generated/` (gitignored, contains real secrets).

```
config/.env                    deploy/gke/ (templates)
     │                              │
     └──── generate-manifests.sh ───┘
                    │
                    ▼
           deploy/generated/     ← ready to kubectl apply
             namespace.yaml
             configmap.yaml
             secrets.yaml        ← real API keys from .env
             agent.yaml          ← real image + Workload Identity
             mcp-servers.yaml    ← real image
             dashboard.yaml      ← real image
```

### Regenerate after changing config

```bash
# Edit your config
vi config/.env

# Regenerate manifests
./scripts/generate-manifests.sh

# Re-apply to cluster
kubectl apply -f deploy/generated/
```

### Override the container image

```bash
# Default: reads from GCP_PROJECT_ID to build Artifact Registry path
# Override with IMAGE env var:
IMAGE=us-central1-docker.pkg.dev/my-project/nightops/nightops-agent:v2 \
  ./scripts/generate-manifests.sh
```

---

## Option A: Deploy to GKE (Recommended)

### 1. Set up the cluster

```bash
export GCP_PROJECT_ID=your-project-id
./scripts/setup-gke.sh
```

This creates:
- GKE cluster with Workload Identity enabled
- GCP service account (`nightops-agent`) with required IAM roles
- Kubernetes service account bound via Workload Identity
- Artifact Registry for container images
- `nightops` namespace
- Auto-generated manifests in `deploy/generated/`

### 2. Configure environment

```bash
cp config/.env.example config/.env
vi config/.env  # fill in your API keys
```

### 3. Build and push image

```bash
export REGION=us-central1
export IMAGE="${REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/nightops/nightops-agent:latest"

docker build -t "$IMAGE" .
docker push "$IMAGE"
```

### 4. Generate and apply manifests

```bash
# Generate rendered manifests with your config
./scripts/generate-manifests.sh

# Apply to cluster
kubectl apply -f deploy/generated/
```

### 5. Verify deployment

```bash
kubectl get pods -n nightops
kubectl logs deployment/nightops-agent -n nightops
```

### 6. Access services

```bash
# Dashboard
kubectl port-forward svc/nightops-dashboard 8888:8888 -n nightops

# Webhook receiver (for Grafana/Alertmanager integration)
kubectl port-forward svc/nightops-webhook 8090:8090 -n nightops
```

---

## Option B: Deploy via Cloud Build

```bash
# Cloud Build handles: build → push → deploy
gcloud builds submit --config cloudbuild.yaml \
  --substitutions=_CLUSTER_NAME=nightops-demo,_CLUSTER_LOCATION=us-central1-a

# Note: You still need to generate and apply secrets separately:
./scripts/generate-manifests.sh
kubectl apply -f deploy/generated/secrets.yaml
```

---

## Option C: Local Development with Docker Compose

```bash
# Configure
cp config/.env.example config/.env
# Edit config/.env with your API keys

# Start all services
docker compose up -d

# View logs
docker compose logs -f webhook-receiver

# Send test webhook
curl -X POST http://localhost:8090/api/v1/webhooks/grafana \
  -H "Content-Type: application/json" \
  -d '{"alerts":[{"labels":{"alertname":"Test","severity":"high"},"status":"firing"}]}'
```

---

## Option D: Local Development without Docker

```bash
# Create venv and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Start webhook receiver (autonomous mode)
nightops agent watch

# Or run interactive investigation
nightops agent run --interactive
```

---

## Live Demo (Meetups / Workshops)

One command deploys the full stack + a broken demo app, triggers an incident, and streams agent logs while the dashboard is accessible.

### One-Command Demo

```bash
# Configure your environment first
cp config/.env.example config/.env
vi config/.env  # fill in GOOGLE_API_KEY and GCP_PROJECT_ID

# Run the full demo (creates cluster, builds images, deploys, triggers incident)
./scripts/demo-gke.sh
```

This will:
1. Set up GKE cluster with Workload Identity + IAM
2. Build and push agent + demo app images to Artifact Registry
3. Generate all K8s manifests from your `config/.env`
4. Deploy the NightOps agent stack (agent, MCP servers, dashboard)
5. Deploy the demo app (3 replicas, healthy)
6. Trigger the incident scenario (default: memory-leak)
7. Port-forward dashboard (:8888), webhook (:8090), and demo app (:8080)
8. Stream agent logs so you can watch it detect and investigate

### Scenario Options

```bash
./scripts/demo-gke.sh --scenario memory-leak       # OOMKill from gradual leak
./scripts/demo-gke.sh --scenario cpu-spike          # CPU throttling from bad endpoint
./scripts/demo-gke.sh --scenario cascading-failure  # DB connection pool exhaustion
./scripts/demo-gke.sh --scenario config-drift       # Bad config → 50% error rate
./scripts/demo-gke.sh --scenario oom-kill            # Aggressive immediate OOM
```

### Speed Options (for repeat demos)

```bash
./scripts/demo-gke.sh --skip-setup                  # Cluster already exists
./scripts/demo-gke.sh --skip-build                   # Images already pushed
./scripts/demo-gke.sh --skip-setup --skip-build      # Just deploy + trigger
./scripts/demo-gke.sh --skip-setup --skip-build --scenario cpu-spike 
```

### During the Demo

| What | URL / Command |
|------|--------------|
| Dashboard | http://localhost:8888 |
| Agent logs | `kubectl logs -f deployment/nightops-agent -n nightops` |
| Watch pods crash | `kubectl get pods -n nightops-demo -w` |
| K8s events | `kubectl get events -n nightops-demo --sort-by='.lastTimestamp'` |
| Demo app metrics | http://localhost:8080/metrics |
| Reset | `./scripts/demo-gke.sh --reset` |

### CLI Demo Commands

```bash
# Also available as CLI commands:
nightops demo deploy                          # Build + push + deploy demo app
nightops demo trigger --scenario memory-leak  # Trigger a scenario
nightops demo reset                           # Reset to clean state
```

---

## Webhook Integration

Configure your alert sources to POST to the webhook receiver:

| Source | Endpoint |
|--------|----------|
| Grafana Alerting | `http://<host>:8090/api/v1/webhooks/grafana` |
| Prometheus Alertmanager | `http://<host>:8090/api/v1/webhooks/alertmanager` |
| PagerDuty | `http://<host>:8090/api/v1/webhooks/pagerduty` |
| Custom/Generic | `http://<host>:8090/api/v1/webhooks/generic` |
| Health Check | `GET http://<host>:8090/api/v1/webhooks/health` |

In GKE, expose via an Ingress or use `kubectl port-forward` for testing.

---

## IAM Roles Reference

The `nightops-agent` GCP service account requires:

| Role | Purpose |
|------|---------|
| `roles/logging.viewer` | Read Cloud Logging data |
| `roles/monitoring.viewer` | Read Cloud Monitoring metrics |
| `roles/container.viewer` | Read GKE resources |
| `roles/container.developer` | Manage workloads (for remediation) |

These are automatically assigned by `setup-gke.sh`.

---

## File Structure

```
deploy/
  gke/                          # Templates (committed to git)
    namespace.yaml              #   nightops namespace
    configmap.yaml              #   nightops.yaml + remediation_policies.yaml
    secrets.yaml                #   Template with placeholder values
    agent.yaml                  #   Agent deployment + RBAC + PVC (AGENT_IMAGE placeholder)
    mcp-servers.yaml            #   4 MCP server Deployments + Services
    dashboard.yaml              #   Dashboard Deployment + Service

  generated/                    # Rendered manifests (gitignored, contains real secrets)
    *.yaml                      #   Auto-generated by scripts/generate-manifests.sh
    demo/                       #   Demo app manifests with real image refs
      demo-app.yaml
      memory-leak-scenario.yaml
      cpu-spike-scenario.yaml

demo/
    Dockerfile                  # Demo app container
    scenarios/
        demo_app.py             # FastAPI app with 5 failure modes
        scenarios.yaml          # Scenario definitions
    k8s_manifests/              # Templates (DEMO_IMAGE placeholder)
        demo-app.yaml
        memory-leak-scenario.yaml
        cpu-spike-scenario.yaml

cloudbuild.yaml                 # Cloud Build pipeline (build → push → deploy)

config/
    .env.example                # Template — copy to .env and fill in values
    .env                        # Your actual config (gitignored)

scripts/
    setup-gke.sh                # GKE cluster + IAM + Workload Identity setup
    generate-manifests.sh       # Renders templates → deploy/generated/
    demo-gke.sh                 # One-command GKE demo (meetups/workshops)
    demo-local.sh               # Local Docker demo
    demo-live.sh                # Interactive conference demo
    cleanup.sh                  # Tear down resources
```

---

## Troubleshooting

**Pods stuck in ImagePullBackOff**
```bash
# Check if image exists in registry
gcloud artifacts docker images list ${REGION}-docker.pkg.dev/${PROJECT_ID}/nightops

# Check pod events
kubectl describe pod -l app.kubernetes.io/name=nightops-agent -n nightops
```

**Agent can't reach MCP servers**
```bash
# Check MCP server pods are running
kubectl get pods -n nightops -l app.kubernetes.io/component=mcp-server

# Check service DNS resolution
kubectl run -it --rm debug --image=busybox -n nightops -- nslookup mcp-kubernetes
```

**Webhook receiver not accepting alerts**
```bash
# Check health endpoint
kubectl port-forward svc/nightops-webhook 8090:8090 -n nightops &
curl http://localhost:8090/api/v1/webhooks/health
```

**Workload Identity not working**
```bash
# Verify annotation
kubectl get sa nightops-agent -n nightops -o yaml | grep gcp-service-account

# Test from pod
kubectl run -it --rm --serviceaccount=nightops-agent test \
  --image=google/cloud-sdk:slim -n nightops -- \
  gcloud auth list
```

**Regenerate manifests after config change**
```bash
vi config/.env
./scripts/generate-manifests.sh
kubectl apply -f deploy/generated/
```
