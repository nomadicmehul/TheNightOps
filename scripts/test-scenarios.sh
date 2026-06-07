#!/usr/bin/env bash
# TheNightOps — Scenario Test Runner
#
# Deploys the standalone demo scenarios (public images, no build) and runs the
# agent against each in Plan A (MCP multi-agent), Plan B (--simple kubectl), or both.
#
# Usage:
#   ./scripts/test-scenarios.sh --deploy                 # deploy workloads + run all 6 in both plans
#   ./scripts/test-scenarios.sh                          # run all 6 in both plans (assumes deployed)
#   ./scripts/test-scenarios.sh --plan a                 # Plan A only
#   ./scripts/test-scenarios.sh --plan b --scenario 3    # Plan B, scenario 3 only
#   ./scripts/test-scenarios.sh --verify-only            # just print cluster state
#   ./scripts/test-scenarios.sh --help
#
# Prerequisites:
#   - nightops CLI installed (pip install -e ".[dev]")
#   - kubectl configured for the target GKE cluster (export USE_GKE_GCLOUD_AUTH_PLUGIN=True)
#   - Gemini auth configured in config/.env (AI Studio key or Vertex AI)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MANIFEST="${PROJECT_ROOT}/demo/k8s_manifests/standalone-scenarios.yaml"

# kubectl against GKE needs the auth plugin.
export USE_GKE_GCLOUD_AUTH_PLUGIN=True

# ── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

# ── Defaults ────────────────────────────────────────────────────────
VERIFY_ONLY=false
DEPLOY=false
PLAN="both"          # a | b | both
SCENARIO_NUM=""

# ── Parse Arguments ─────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --deploy) DEPLOY=true; shift ;;
        --verify-only) VERIFY_ONLY=true; shift ;;
        --plan) PLAN="$2"; shift 2 ;;
        --scenario|-s) SCENARIO_NUM="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: ./scripts/test-scenarios.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --deploy           Apply demo/k8s_manifests/standalone-scenarios.yaml first"
            echo "  --plan a|b|both    Plan A (MCP), Plan B (--simple), or both (default: both)"
            echo "  --scenario, -s N   Run only scenario N (1-6)"
            echo "  --verify-only      Skip investigations, print cluster state only"
            echo "  --help, -h         Show this help"
            echo ""
            echo "Scenarios:"
            echo "  1  OOMKill            (demo-api)       300M alloc vs 100Mi limit"
            echo "  2  Config drift       (payment-api)    missing env -> exit 1 -> CrashLoopBackOff"
            echo "  3  Bad image          (inventory-api)  nonexistent tag -> ImagePullBackOff"
            echo "  4  Failed scheduling  (report-batch)   64 CPU request -> Pending"
            echo "  5  Readiness probe    (frontend-web)   wrong probe port -> never Ready"
            echo "  6  Cascading          (checkout/cart)  shared missing Secret"
            exit 0 ;;
        *) echo -e "${RED}Error: Unknown option '$1'${RESET}"; echo "Run with --help for usage."; exit 1 ;;
    esac
done

if [[ -n "${SCENARIO_NUM}" ]] && ! [[ "${SCENARIO_NUM}" =~ ^[1-6]$ ]]; then
    echo -e "${RED}Error: --scenario must be 1-6${RESET}"; exit 1
fi
if [[ "${PLAN}" != "a" && "${PLAN}" != "b" && "${PLAN}" != "both" ]]; then
    echo -e "${RED}Error: --plan must be a, b, or both${RESET}"; exit 1
fi

# ── Scenario Definitions (match standalone-scenarios.yaml) ──────────
SCENARIO_NAMES=(
    "OOMKill (demo-api)"
    "Config drift (payment-api)"
    "Bad image (inventory-api)"
    "Failed scheduling (report-batch)"
    "Readiness probe (frontend-web)"
    "Cascading / missing secret (checkout-api + cart-api)"
)
SCENARIO_INCIDENTS=(
    "Pods in the nightops-demo namespace (deployment demo-api) are being OOMKilled and entering CrashLoopBackOff. Investigate the root cause and recommend remediation."
    "Deployment payment-api in namespace nightops-demo is in CrashLoopBackOff: pods start then exit with an error shortly after launch. Investigate the root cause and recommend remediation."
    "Deployment inventory-api in namespace nightops-demo has pods that never reach Ready and never start running. Investigate the root cause and recommend remediation."
    "Deployment report-batch in namespace nightops-demo has a pod stuck in Pending that never schedules onto a node. Investigate the root cause and recommend remediation."
    "Pods for deployment frontend-web in namespace nightops-demo are Running but never become Ready (0/1). Investigate the root cause and recommend remediation."
    "Multiple payment-tier services (checkout-api and cart-api) in namespace nightops-demo are failing to start at the same time. Investigate the root cause, determine if they share a common cause, and recommend remediation."
)

# ── Helpers ─────────────────────────────────────────────────────────
separator() { echo ""; echo -e "${DIM}$(printf '%.0s─' {1..80})${RESET}"; echo ""; }
banner() {
    echo ""
    echo -e "${BOLD}${BLUE}╔$(printf '%.0s═' {1..78})╗${RESET}"
    printf "${BOLD}${BLUE}║${RESET} ${BOLD}%-76s ${BLUE}║${RESET}\n" "$1"
    echo -e "${BOLD}${BLUE}╚$(printf '%.0s═' {1..78})╝${RESET}"
    echo ""
}
status_msg() { echo -e "$1[$2]${RESET} $3"; }

run_one_plan() {
    local plan="$1" incident="$2" flag label start end
    if [[ "${plan}" == "a" ]]; then flag=""; label="Plan A (MCP multi-agent)";
    else flag="--simple"; label="Plan B (simple kubectl)"; fi

    status_msg "${CYAN}" "START" "${label}"
    start=$(date +%s)
    # shellcheck disable=SC2086
    if nightops agent run ${flag} --incident "${incident}"; then
        end=$(date +%s)
        status_msg "${GREEN}" "DONE" "${label} completed in $((end - start))s"
    else
        end=$(date +%s)
        status_msg "${YELLOW}" "WARN" "${label} exited non-zero after $((end - start))s"
    fi
}

run_scenario() {
    local num="$1" idx=$(( $1 - 1 ))
    banner "Scenario ${num}/6: ${SCENARIO_NAMES[$idx]}"
    echo -e "${DIM}Incident: ${SCENARIO_INCIDENTS[$idx]}${RESET}"; echo ""
    if [[ "${PLAN}" == "a" || "${PLAN}" == "both" ]]; then run_one_plan a "${SCENARIO_INCIDENTS[$idx]}"; echo ""; fi
    if [[ "${PLAN}" == "b" || "${PLAN}" == "both" ]]; then run_one_plan b "${SCENARIO_INCIDENTS[$idx]}"; fi
    separator
}

deploy_scenarios() {
    banner "Deploying standalone scenarios"
    kubectl apply -f "${MANIFEST}"
    echo ""
    status_msg "${CYAN}" "WAIT" "Letting workloads reach their failure states (~30s)..."
    sleep 30
    kubectl get pods -n nightops-demo
    separator
}

run_verification() {
    banner "Verification: Cluster State"
    status_msg "${CYAN}" "CHECK" "Pods in nightops-demo"; echo ""
    kubectl get pods -n nightops-demo -o wide 2>/dev/null || status_msg "${YELLOW}" "SKIP" "kubectl get pods failed"
    separator
    status_msg "${CYAN}" "CHECK" "Recent events in nightops-demo"; echo ""
    kubectl get events -n nightops-demo --sort-by='.lastTimestamp' 2>/dev/null | tail -20 || status_msg "${YELLOW}" "SKIP" "kubectl get events failed"
    echo ""
}

# ── Virtualenv ──────────────────────────────────────────────────────
cd "${PROJECT_ROOT}"
if [[ -d ".venv" ]]; then source .venv/bin/activate
elif [[ -d "venv" ]]; then source venv/bin/activate
else status_msg "${YELLOW}" "WARN" "No virtualenv found — using system Python"; fi

command -v nightops &>/dev/null || { echo -e "${RED}Error: 'nightops' not found. Run: pip install -e \".[dev]\"${RESET}"; exit 1; }
command -v kubectl &>/dev/null || { echo -e "${RED}Error: 'kubectl' not found.${RESET}"; exit 1; }

# ── Main ────────────────────────────────────────────────────────────
banner "TheNightOps — Scenario Test Runner"
echo -e "  ${BOLD}Cluster:${RESET} $(kubectl config current-context 2>/dev/null || echo 'unknown')"
echo -e "  ${BOLD}Plan:${RESET}    ${PLAN}"
echo -e "  ${BOLD}Mode:${RESET}    $(if ${VERIFY_ONLY}; then echo 'verify only'; elif [[ -n "${SCENARIO_NUM}" ]]; then echo "scenario ${SCENARIO_NUM}"; else echo 'all scenarios'; fi)"
echo ""

TOTAL_START=$(date +%s)
${DEPLOY} && deploy_scenarios

if [[ "${VERIFY_ONLY}" == "true" ]]; then
    run_verification
elif [[ -n "${SCENARIO_NUM}" ]]; then
    run_scenario "${SCENARIO_NUM}"; run_verification
else
    for i in 1 2 3 4 5 6; do run_scenario "${i}"; done
    run_verification
fi

separator
status_msg "${GREEN}" "COMPLETE" "Finished in $(( $(date +%s) - TOTAL_START ))s"
echo ""
