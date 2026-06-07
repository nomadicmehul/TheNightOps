#!/usr/bin/env bash
# TheNightOps — Cleanup Script
# Removes demo resources and optionally deletes the GKE cluster.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/config/.env"

# Auto-load config/.env if available
if [[ -f "${ENV_FILE}" ]]; then
    set -a
    source "${ENV_FILE}"
    set +a
fi

PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID in config/.env or environment}"
CLUSTER_NAME="${GKE_CLUSTER_NAME:-nightops-demo}"
ZONE="${GKE_CLUSTER_LOCATION:-${GCP_ZONE:-us-central1-a}}"

# Flags: --delete-cluster / -y / --yes delete the cluster non-interactively.
DELETE_CLUSTER=false
for arg in "$@"; do
    case "${arg}" in
        --delete-cluster|-y|--yes) DELETE_CLUSTER=true ;;
        -h|--help)
            echo "Usage: cleanup.sh [--delete-cluster|-y]"
            echo "  Removes demo/agent namespaces and AR images."
            echo "  --delete-cluster / -y : also delete the GKE cluster (stops billing) without prompting."
            exit 0 ;;
        *) echo "Unknown argument: ${arg}"; exit 1 ;;
    esac
done

echo "TheNightOps — Cleanup"
echo "====================="

# Remove demo namespace
echo "→ Removing demo namespace..."
kubectl delete namespace nightops-demo --ignore-not-found=true
echo "  ✓ Demo namespace removed"

# Remove agent namespace
echo "→ Removing NightOps agent namespace..."
kubectl delete namespace nightops --ignore-not-found=true
echo "  ✓ NightOps namespace removed"

# Remove container images from Artifact Registry
REGION="${GCP_REGION:-us-central1}"
echo "→ Removing container images from Artifact Registry..."
gcloud artifacts docker images delete \
    "${REGION}-docker.pkg.dev/${PROJECT_ID}/nightops/nightops-agent:latest" \
    --quiet 2>/dev/null || true
gcloud artifacts docker images delete \
    "${REGION}-docker.pkg.dev/${PROJECT_ID}/nightops/nightops-demo-api:latest" \
    --quiet 2>/dev/null || true
echo "  ✓ Images cleaned up"

# Delete the cluster — this is the resource that actually bills (~3 nodes).
if [[ "${DELETE_CLUSTER}" != "true" ]]; then
    echo ""
    echo "⚠ The GKE cluster '${CLUSTER_NAME}' keeps billing while it runs (~3 nodes)."
    read -p "Delete it now to stop charges? (y/N) " -n 1 -r
    echo
fi
if [[ "${DELETE_CLUSTER}" == "true" || ${REPLY:-} =~ ^[Yy]$ ]]; then
    echo "→ Deleting GKE cluster..."
    gcloud container clusters delete "${CLUSTER_NAME}" \
        --project="${PROJECT_ID}" \
        --zone="${ZONE}" \
        --quiet
    echo "  ✓ Cluster deleted — billing stopped."
else
    echo "  Cluster PRESERVED — it is still billing."
    echo "  To delete later: gcloud container clusters delete ${CLUSTER_NAME} --zone ${ZONE} --project ${PROJECT_ID} --quiet"
fi

echo ""
echo "✓ Cleanup complete!"
