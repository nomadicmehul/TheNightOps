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

# ── Preflight (fail fast before ~15 min of cluster creation) ───
echo "→ Preflight checks..."
command -v gcloud >/dev/null || { echo "  ✗ gcloud not found — install the Google Cloud SDK."; exit 1; }
command -v kubectl >/dev/null || { echo "  ✗ kubectl not found — 'gcloud components install kubectl'."; exit 1; }

if [[ -z "$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null)" ]]; then
    echo "  ✗ No active gcloud account. Run: gcloud auth login"; exit 1
fi
if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
    echo "  ✗ Application Default Credentials missing. Run: gcloud auth application-default login"; exit 1
fi

BILLING="$(gcloud beta billing projects describe "${PROJECT_ID}" --format='value(billingEnabled)' 2>/dev/null || echo unknown)"
if [[ "${BILLING}" == "False" ]]; then
    echo "  ✗ Billing is not enabled on project '${PROJECT_ID}'. Enable it before creating a cluster."; exit 1
elif [[ "${BILLING}" == "unknown" ]]; then
    echo "  ⚠ Could not verify billing (try 'gcloud components install beta'). Continuing."
fi

# kubectl against GKE needs the auth plugin; install it now so the kubectl
# steps later in THIS script don't fail with 'gke-gcloud-auth-plugin not found'.
export USE_GKE_GCLOUD_AUTH_PLUGIN=True
if ! gke-gcloud-auth-plugin --version >/dev/null 2>&1; then
    echo "  Installing gke-gcloud-auth-plugin..."
    gcloud components install gke-gcloud-auth-plugin --quiet
fi
echo "  ✓ Preflight passed (auth, ADC, billing, gke-gcloud-auth-plugin)"

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

# ── Official Google Cloud MCP servers ────────────────────────
# No "enable" step is required. The GKE and Cloud Observability MCP endpoints
# (container.googleapis.com/mcp, logging.googleapis.com/mcp) authenticate via
# Application Default Credentials + the roles/mcp.toolUser binding granted below.
# (There is no `gcloud beta services mcp enable` subcommand.)

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
    # Suppress only the noisy policy dump on stdout; let real errors surface on stderr.
    if gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="${role}" \
        --condition=None \
        --quiet >/dev/null; then
        echo "  ✓ ${role}"
    else
        echo "  ✗ Failed to bind ${role}"; exit 1
    fi
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
    --quiet >/dev/null

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
        --quiet >/dev/null
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

# ── Gemini auth reminder ──────────────────────────────────────
echo ""
echo "→ Gemini auth (the agent's LLM) — pick ONE in config/.env:"
echo "    • AI Studio key:  GOOGLE_API_KEY=<key from aistudio.google.com/apikey>"
echo "    • Vertex AI:      GOOGLE_GENAI_USE_VERTEXAI=TRUE, GOOGLE_CLOUD_PROJECT=${PROJECT_ID},"
echo "                      GOOGLE_CLOUD_LOCATION=${REGION}  (run: gcloud services enable aiplatform.googleapis.com)"
echo "  Note: if AI Studio returns 429 (no credits), switch to Vertex."
echo "  On Vertex, set NIGHTOPS_MODEL=gemini-2.5-flash (gemini-3.1-pro-preview is not on Vertex)."

# ── Generate Manifests (optional — only for deploying the agent INTO GKE) ──
# Non-fatal: local runs (run-local.sh / nightops agent run) don't need these,
# so a failure here must not abort an otherwise-successful cluster setup.
echo ""
echo "→ Generating K8s manifests from config/.env..."
if [[ -f "${SCRIPT_DIR}/generate-manifests.sh" ]]; then
    if "${SCRIPT_DIR}/generate-manifests.sh"; then
        echo ""
        echo "  ✓ Manifests generated in deploy/generated/"
    else
        echo ""
        echo "  ⚠ Manifest generation skipped (only needed to deploy the agent INTO GKE)."
        echo "    Local runs don't need it. Fix config/.env and re-run generate-manifests.sh if you want them."
    fi
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
