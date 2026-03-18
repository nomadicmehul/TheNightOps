#!/usr/bin/env bash
# TheNightOps — GKE Cluster Setup Script
#
# Creates a GKE cluster with Workload Identity, configures IAM roles,
# enables MCP servers, and prepares the cluster for TheNightOps agent deployment.
#
# Usage:
#   export GCP_PROJECT_ID=my-project
#   ./scripts/setup-gke.sh
#
# Optional overrides:
#   CLUSTER_NAME, GCP_REGION, GCP_ZONE, MACHINE_TYPE, NUM_NODES

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Configuration ──────────────────────────────────────────────
PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID environment variable}"
CLUSTER_NAME="${CLUSTER_NAME:-nightops-demo}"
REGION="${GCP_REGION:-us-central1}"
ZONE="${GCP_ZONE:-us-central1-a}"
MACHINE_TYPE="${MACHINE_TYPE:-e2-standard-2}"
NUM_NODES="${NUM_NODES:-3}"
SA_NAME="nightops-agent"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "╔═══════════════════════════════════════════════╗"
echo "║       TheNightOps — GKE Cluster Setup          ║"
echo "╠═══════════════════════════════════════════════╣"
echo "║ Project:   ${PROJECT_ID}"
echo "║ Cluster:   ${CLUSTER_NAME}"
echo "║ Zone:      ${ZONE}"
echo "║ Machines:  ${MACHINE_TYPE} x ${NUM_NODES}"
echo "║ SA:        ${SA_EMAIL}"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# ── Enable Required APIs ───────────────────────────────────────
echo "→ Enabling required GCP APIs..."
gcloud services enable \
    container.googleapis.com \
    logging.googleapis.com \
    monitoring.googleapis.com \
    cloudresourcemanager.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    iam.googleapis.com \
    --project="${PROJECT_ID}"
echo "  ✓ APIs enabled"

# ── Enable MCP for GKE and Cloud Logging ─────────────────────
echo ""
echo "→ Enabling MCP (Model Context Protocol) on GCP services..."

# GKE MCP — required for the agent to query K8s resources via MCP
echo "  Enabling GKE MCP (container.googleapis.com)..."
if gcloud beta services mcp enable container.googleapis.com --project="${PROJECT_ID}" 2>/dev/null; then
    echo "  ✓ GKE MCP enabled"
else
    echo "  ⚠ GKE MCP enable failed (may need 'gcloud components update' or beta access)"
    echo "    Try manually: gcloud beta services mcp enable container.googleapis.com --project=${PROJECT_ID}"
fi

# Cloud Logging MCP — required for log analysis via MCP
echo "  Enabling Cloud Logging MCP (logging.googleapis.com)..."
if gcloud beta services mcp enable logging.googleapis.com --project="${PROJECT_ID}" 2>/dev/null; then
    echo "  ✓ Cloud Logging MCP enabled"
else
    echo "  ⚠ Cloud Logging MCP enable failed"
    echo "    Try manually: gcloud beta services mcp enable logging.googleapis.com --project=${PROJECT_ID}"
fi

# ── Create GKE Cluster with Workload Identity ─────────────────
echo ""
echo "→ Creating GKE cluster '${CLUSTER_NAME}'..."
if gcloud container clusters describe "${CLUSTER_NAME}" --zone="${ZONE}" --project="${PROJECT_ID}" &>/dev/null; then
    echo "  ✓ Cluster already exists, skipping creation"
else
    gcloud container clusters create "${CLUSTER_NAME}" \
        --project="${PROJECT_ID}" \
        --zone="${ZONE}" \
        --machine-type="${MACHINE_TYPE}" \
        --num-nodes="${NUM_NODES}" \
        --enable-autorepair \
        --enable-autoupgrade \
        --workload-pool="${PROJECT_ID}.svc.id.goog" \
        --logging=SYSTEM,WORKLOAD \
        --monitoring=SYSTEM \
        --labels="app=nightops,env=demo"
    echo "  ✓ Cluster created with Workload Identity"
fi

# ── Get Credentials ────────────────────────────────────────────
echo ""
echo "→ Fetching cluster credentials..."
gcloud container clusters get-credentials "${CLUSTER_NAME}" \
    --project="${PROJECT_ID}" \
    --zone="${ZONE}"
echo "  ✓ kubectl configured"

# ── Create GCP Service Account ─────────────────────────────────
echo ""
echo "→ Creating GCP service account '${SA_NAME}'..."
if gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" &>/dev/null; then
    echo "  ✓ Service account already exists"
else
    gcloud iam service-accounts create "${SA_NAME}" \
        --project="${PROJECT_ID}" \
        --display-name="TheNightOps Agent" \
        --description="Service account for TheNightOps autonomous SRE agent"
    echo "  ✓ Service account created"
fi

# ── Assign IAM Roles ──────────────────────────────────────────
echo ""
echo "→ Assigning IAM roles to ${SA_EMAIL}..."

ROLES=(
    "roles/logging.viewer"        # Read Cloud Logging
    "roles/monitoring.viewer"     # Read Cloud Monitoring
    "roles/container.viewer"      # Read GKE resources
    "roles/container.developer"   # Manage GKE workloads (for remediation)
    "roles/mcp.toolUser"          # Access GCP MCP servers (GKE MCP, Logging MCP)
)

for role in "${ROLES[@]}"; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="${role}" \
        --condition=None \
        --quiet 2>/dev/null
    echo "  ✓ ${role}"
done

# ── Bind Workload Identity ─────────────────────────────────────
echo ""
echo "→ Setting up Workload Identity binding..."

# Create nightops namespace
kubectl create namespace nightops 2>/dev/null || echo "  Namespace 'nightops' already exists"

# Create K8s service account
kubectl create serviceaccount nightops-agent -n nightops 2>/dev/null || echo "  K8s SA 'nightops-agent' already exists"

# Bind K8s SA to GCP SA
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
    --project="${PROJECT_ID}" \
    --role="roles/iam.workloadIdentityUser" \
    --member="serviceAccount:${PROJECT_ID}.svc.id.goog[nightops/nightops-agent]" \
    --quiet 2>/dev/null

kubectl annotate serviceaccount nightops-agent \
    -n nightops \
    --overwrite \
    "iam.gke.io/gcp-service-account=${SA_EMAIL}"

echo "  ✓ Workload Identity bound: nightops/nightops-agent → ${SA_EMAIL}"

# ── Grant MCP Role to Current User (for local development) ───
echo ""
echo "→ Granting MCP role to current user (for local run-local.sh)..."
CURRENT_USER=$(gcloud config get-value account 2>/dev/null)
if [[ -n "${CURRENT_USER}" ]]; then
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="user:${CURRENT_USER}" \
        --role="roles/mcp.toolUser" \
        --condition=None \
        --quiet 2>/dev/null
    echo "  ✓ roles/mcp.toolUser granted to ${CURRENT_USER}"
else
    echo "  ⚠ Could not determine current gcloud user"
    echo "    Run manually: gcloud projects add-iam-policy-binding ${PROJECT_ID} --member=user:YOU@EMAIL --role=roles/mcp.toolUser"
fi

# ── Create Artifact Registry ──────────────────────────────────
echo ""
echo "→ Creating Artifact Registry repository..."
gcloud artifacts repositories describe nightops \
    --location="${REGION}" --project="${PROJECT_ID}" &>/dev/null \
|| gcloud artifacts repositories create nightops \
    --repository-format=docker \
    --location="${REGION}" \
    --project="${PROJECT_ID}" \
    --description="TheNightOps container images"
echo "  ✓ Artifact Registry ready"

# ── Verify ─────────────────────────────────────────────────────
echo ""
echo "→ Verifying cluster..."
kubectl cluster-info
kubectl get nodes
echo ""
echo "  ✓ Cluster is ready!"

# ── Generate Manifests ────────────────────────────────────────
echo ""
echo "→ Generating K8s manifests from config/.env..."
if [[ -f "${SCRIPT_DIR}/generate-manifests.sh" ]]; then
    "${SCRIPT_DIR}/generate-manifests.sh"
    echo ""
    echo "  ✓ Manifests generated in deploy/generated/"
else
    echo "  ⚠ scripts/generate-manifests.sh not found, skipping"
fi

# ── Print Next Steps ──────────────────────────────────────────
echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║                      Next Steps                            ║"
echo "╠════════════════════════════════════════════════════════════╣"
echo "║                                                            ║"
echo "║  Option A — Run locally (recommended for testing):         ║"
echo "║    gcloud auth application-default login                   ║"
echo "║    bash scripts/run-local.sh                               ║"
echo "║    → Dashboard: http://localhost:8888                      ║"
echo "║                                                            ║"
echo "║  Option B — Deploy to GKE:                                 ║"
echo "║    1. Ensure config/.env has your API keys                 ║"
echo "║    2. ./scripts/generate-manifests.sh                      ║"
echo "║    3. kubectl apply -f deploy/generated/                   ║"
echo "║    4. kubectl port-forward svc/nightops-dashboard \        ║"
echo "║         8888:8888 -n nightops                              ║"
echo "║                                                            ║"
echo "║  Option C — Full demo (cluster + demo app + incident):    ║"
echo "║    ./scripts/demo-gke.sh --skip-setup                     ║"
echo "║                                                            ║"
echo "╚════════════════════════════════════════════════════════════╝"
