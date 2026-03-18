#!/usr/bin/env bash
# TheNightOps — One-Command GKE Demo
#
# Deploys the full NightOps stack + demo app to GKE, triggers an incident,
# and opens the dashboard so you can watch the agent detect and heal it.
#
# Perfect for meetups, workshops, and live demos.
#
# Usage:
#   ./scripts/demo-gke.sh                           # Full setup + memory-leak demo
#   ./scripts/demo-gke.sh --scenario cpu-spike       # Use a different scenario
#   ./scripts/demo-gke.sh --skip-setup               # Skip cluster setup (already done)
#   ./scripts/demo-gke.sh --skip-build               # Skip image builds (already pushed)
#   ./scripts/demo-gke.sh --reset                    # Reset demo to clean state
#
# Scenarios: memory-leak, cpu-spike, cascading-failure, config-drift, oom-kill
#
# Prerequisites:
#   - config/.env with at least GOOGLE_API_KEY and GCP_PROJECT_ID
#   - gcloud CLI authenticated
#   - docker running

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/config/.env"

# ── Defaults ──────────────────────────────────────────────────────
SCENARIO="memory-leak"
SKIP_SETUP=false
SKIP_BUILD=false
RESET_ONLY=false

# ── Parse Arguments ───────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --scenario|-s) SCENARIO="$2"; shift 2 ;;
        --skip-setup)  SKIP_SETUP=true; shift ;;
        --skip-build)  SKIP_BUILD=true; shift ;;
        --reset)       RESET_ONLY=true; shift ;;
        --help|-h)
            echo "Usage: ./scripts/demo-gke.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --scenario, -s  Failure scenario (default: memory-leak)"
            echo "  --skip-setup    Skip GKE cluster setup"
            echo "  --skip-build    Skip Docker image builds"
            echo "  --reset         Reset demo to clean state and exit"
            echo ""
            echo "Scenarios:"
            echo "  memory-leak         OOMKill from gradual memory leak"
            echo "  cpu-spike           CPU throttling from bad endpoint"
            echo "  cascading-failure   Database connection pool exhaustion"
            echo "  config-drift        Bad config causing 50% errors"
            echo "  oom-kill            Aggressive immediate OOM"
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Load Environment ──────────────────────────────────────────────
if [[ -f "${ENV_FILE}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
else
    echo "✗ config/.env not found"
    echo "  Run: cp config/.env.example config/.env"
    echo "  Then fill in GOOGLE_API_KEY and GCP_PROJECT_ID"
    exit 1
fi

PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID in config/.env}"
REGION="${GCP_REGION:-us-central1}"
ZONE="${GKE_CLUSTER_LOCATION:-${GCP_ZONE:-us-central1-a}}"
CLUSTER_NAME="${GKE_CLUSTER_NAME:-nightops-demo}"
AGENT_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/nightops/nightops-agent:latest"
DEMO_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/nightops/nightops-demo-api:latest"

# ── Colors ────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

banner() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}  ${BOLD}$1${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

step() { echo -e "  ${CYAN}→${NC} $1"; }
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }

wait_for_pods() {
    local namespace=$1
    local label=$2
    local timeout=${3:-120}
    local elapsed=0

    step "Waiting for pods (${namespace}/${label}) to be ready..."
    while [[ $elapsed -lt $timeout ]]; do
        local ready
        ready=$(kubectl get pods -n "${namespace}" -l "${label}" -o jsonpath='{.items[*].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "")
        if [[ -n "$ready" ]] && ! echo "$ready" | grep -q "False"; then
            ok "All pods ready in ${namespace}"
            return 0
        fi
        sleep 5
        elapsed=$((elapsed + 5))
        echo -ne "\r  … waiting (${elapsed}s/${timeout}s)"
    done
    echo ""
    warn "Timeout waiting for pods — continuing anyway"
}

# ── Handle Reset ──────────────────────────────────────────────────
if [[ "$RESET_ONLY" == true ]]; then
    banner "Resetting Demo Environment"
    kubectl set env deployment/demo-api -n nightops-demo FAILURE_MODE=none ERROR_RATE_PERCENT=0 2>/dev/null || true
    kubectl rollout restart deployment/demo-api -n nightops-demo 2>/dev/null || true
    ok "Demo reset to clean state"
    exit 0
fi

# ── Banner ────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║                                                      ║"
echo "  ║        🌙  TheNightOps — GKE Live Demo  🌙          ║"
echo "  ║                                                      ║"
echo "  ║   Autonomous SRE Agent for Kubernetes                ║"
echo "  ║                                                      ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  Project:   ${BOLD}${PROJECT_ID}${NC}"
echo -e "  Cluster:   ${BOLD}${CLUSTER_NAME}${NC}"
echo -e "  Scenario:  ${BOLD}${SCENARIO}${NC}"
echo ""

# ══════════════════════════════════════════════════════════════════
# STEP 1: Cluster Setup
# ══════════════════════════════════════════════════════════════════
if [[ "$SKIP_SETUP" == true ]]; then
    banner "Step 1/7: Cluster Setup [SKIPPED]"
    step "Getting cluster credentials..."
    gcloud container clusters get-credentials "${CLUSTER_NAME}" \
        --project="${PROJECT_ID}" --zone="${ZONE}" --quiet
    ok "kubectl configured"
else
    banner "Step 1/7: Setting Up GKE Cluster"
    "${SCRIPT_DIR}/setup-gke.sh"
fi

# ══════════════════════════════════════════════════════════════════
# STEP 2: Build & Push Images
# ══════════════════════════════════════════════════════════════════
if [[ "$SKIP_BUILD" == true ]]; then
    banner "Step 2/7: Build Images [SKIPPED]"
else
    banner "Step 2/7: Building & Pushing Container Images (multi-arch)"

    # Configure Docker for Artifact Registry
    step "Configuring Docker for Artifact Registry..."
    gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet 2>/dev/null
    ok "Docker configured"

    # Ensure buildx builder exists for multi-platform builds
    step "Setting up Docker buildx for multi-platform builds..."
    if ! docker buildx inspect nightops-builder &>/dev/null; then
        docker buildx create --name nightops-builder --use --bootstrap
    else
        docker buildx use nightops-builder
    fi
    ok "Buildx ready (linux/amd64 + linux/arm64)"

    # Build + push agent image (multi-arch)
    step "Building & pushing agent image (amd64 + arm64)..."
    docker buildx build \
        --platform linux/amd64,linux/arm64 \
        -t "${AGENT_IMAGE}" \
        -f "${PROJECT_ROOT}/Dockerfile" \
        "${PROJECT_ROOT}" \
        --push
    ok "Agent image pushed → ${AGENT_IMAGE}"

    # Build + push demo app image (multi-arch)
    step "Building & pushing demo app image (amd64 + arm64)..."
    docker buildx build \
        --platform linux/amd64,linux/arm64 \
        -t "${DEMO_IMAGE}" \
        -f "${PROJECT_ROOT}/demo/Dockerfile" \
        "${PROJECT_ROOT}/demo/" \
        --push
    ok "Demo app image pushed → ${DEMO_IMAGE}"
fi

# ══════════════════════════════════════════════════════════════════
# STEP 3: Generate Manifests
# ══════════════════════════════════════════════════════════════════
banner "Step 3/7: Generating Kubernetes Manifests"

IMAGE="${AGENT_IMAGE}" DEMO_IMAGE="${DEMO_IMAGE}" "${SCRIPT_DIR}/generate-manifests.sh"

# ══════════════════════════════════════════════════════════════════
# STEP 4: Deploy NightOps Agent
# ══════════════════════════════════════════════════════════════════
banner "Step 4/7: Deploying NightOps Agent"

step "Applying agent manifests..."
kubectl apply -f "${PROJECT_ROOT}/deploy/generated/" 2>/dev/null
ok "Agent stack deployed to 'nightops' namespace"

wait_for_pods "nightops" "app.kubernetes.io/name=nightops-agent" 120

# Show what's running
echo ""
kubectl get pods -n nightops --no-headers 2>/dev/null | while read -r line; do
    echo -e "  ${GREEN}│${NC} ${line}"
done

# ══════════════════════════════════════════════════════════════════
# STEP 5: Deploy Demo App (Healthy)
# ══════════════════════════════════════════════════════════════════
banner "Step 5/7: Deploying Demo Application (Healthy)"

step "Applying demo app manifests..."
kubectl apply -f "${PROJECT_ROOT}/deploy/generated/demo/demo-app.yaml"
ok "Demo app deployed to 'nightops-demo' namespace"

wait_for_pods "nightops-demo" "app=demo-api" 90

echo ""
kubectl get pods -n nightops-demo --no-headers 2>/dev/null | while read -r line; do
    echo -e "  ${GREEN}│${NC} ${line}"
done

# ══════════════════════════════════════════════════════════════════
# STEP 6: Trigger Incident Scenario
# ══════════════════════════════════════════════════════════════════
banner "Step 6/7: Triggering Incident — ${SCENARIO}"

GENERATED_DEMO_DIR="${PROJECT_ROOT}/deploy/generated/demo"

case "${SCENARIO}" in
    memory-leak)
        echo -e "  ${YELLOW}Scenario: Memory leak causing OOMKill${NC}"
        echo "  Pods will gradually consume memory until hitting 256Mi limit"
        echo "  and get OOMKilled → CrashLoopBackOff cycle."
        echo ""
        kubectl apply -f "${GENERATED_DEMO_DIR}/memory-leak-scenario.yaml"
        ;;
    cpu-spike)
        echo -e "  ${YELLOW}Scenario: CPU spike from bad endpoint${NC}"
        echo "  The /api/reports/generate endpoint will consume excessive CPU"
        echo "  causing throttling and high latency across all endpoints."
        echo ""
        kubectl apply -f "${GENERATED_DEMO_DIR}/cpu-spike-scenario.yaml"
        ;;
    cascading-failure)
        echo -e "  ${YELLOW}Scenario: Database connection pool exhaustion${NC}"
        echo "  Connection pool is exhausted, causing cascading timeouts."
        echo ""
        kubectl set env deployment/demo-api -n nightops-demo FAILURE_MODE=cascading-failure
        ;;
    config-drift)
        echo -e "  ${YELLOW}Scenario: Bad config causing 50% error rate${NC}"
        echo "  ERROR_RATE_PERCENT set to 50, half of requests fail with 500."
        echo ""
        kubectl set env deployment/demo-api -n nightops-demo FAILURE_MODE=config-drift ERROR_RATE_PERCENT=50
        ;;
    oom-kill)
        echo -e "  ${YELLOW}Scenario: Aggressive OOM kill${NC}"
        echo "  Immediate memory pressure, pods OOMKilled rapidly."
        echo ""
        kubectl set env deployment/demo-api -n nightops-demo FAILURE_MODE=oom-kill
        ;;
    *)
        fail "Unknown scenario: ${SCENARIO}"
        ;;
esac

ok "Scenario '${SCENARIO}' triggered!"
echo ""
echo -e "  ${YELLOW}The incident will develop over the next 2-5 minutes.${NC}"
echo "  The NightOps agent (webhook receiver + event watcher) will"
echo "  automatically detect the issue and begin investigation."

# ══════════════════════════════════════════════════════════════════
# STEP 7: Open Dashboard + Show Status
# ══════════════════════════════════════════════════════════════════
banner "Step 7/7: Access Points"

# Start port-forwards in background
step "Starting port-forwards..."
kubectl port-forward svc/nightops-dashboard 8888:8888 -n nightops &>/dev/null &
PF_DASHBOARD=$!
kubectl port-forward svc/nightops-webhook 8090:8090 -n nightops &>/dev/null &
PF_WEBHOOK=$!
kubectl port-forward svc/demo-api 8080:80 -n nightops-demo &>/dev/null &
PF_DEMO=$!

sleep 2

echo ""
echo -e "  ${BOLD}Dashboard:${NC}        http://localhost:8888"
echo -e "  ${BOLD}Webhook Health:${NC}   http://localhost:8090/api/v1/webhooks/health"
echo -e "  ${BOLD}Demo App:${NC}         http://localhost:8080/healthz"
echo -e "  ${BOLD}Demo Metrics:${NC}     http://localhost:8080/metrics"
echo ""

echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${GREEN}Demo is running!${NC} Here's what to show your audience:"
echo ""
echo "  1. Open the dashboard:   http://localhost:8888"
echo "  2. Watch agent logs:     kubectl logs -f deployment/nightops-agent -n nightops"
echo "  3. Watch demo app:       kubectl get pods -n nightops-demo -w"
echo "  4. Check metrics:        curl http://localhost:8080/metrics"
echo ""
echo -e "  ${YELLOW}Useful commands during the demo:${NC}"
echo "  kubectl get events -n nightops-demo --sort-by='.lastTimestamp'"
echo "  kubectl top pods -n nightops-demo"
echo "  kubectl describe pod -l app=demo-api -n nightops-demo"
echo ""
echo -e "  ${CYAN}To reset:${NC}  ./scripts/demo-gke.sh --reset"
echo -e "  ${CYAN}To stop:${NC}   Press Ctrl+C"
echo ""

# ── Cleanup on exit ───────────────────────────────────────────────
cleanup() {
    echo ""
    echo -e "${CYAN}Stopping port-forwards...${NC}"
    kill "${PF_DASHBOARD}" "${PF_WEBHOOK}" "${PF_DEMO}" 2>/dev/null || true
    echo -e "${GREEN}Done. Demo resources are still running in GKE.${NC}"
    echo ""
    echo "To clean up:"
    echo "  kubectl delete namespace nightops-demo    # Remove demo app"
    echo "  kubectl delete namespace nightops          # Remove NightOps agent"
    echo "  ./scripts/cleanup.sh                       # Full teardown"
}
trap cleanup EXIT

# Keep script running for port-forwards
echo -e "${BOLD}Streaming agent logs (Ctrl+C to stop)...${NC}"
echo ""
kubectl logs -f deployment/nightops-agent -n nightops 2>/dev/null || \
    echo "Waiting for agent pod to start... (press Ctrl+C to stop waiting)"

# If logs exit, just wait
wait
