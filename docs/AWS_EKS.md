# THE NightOps on Amazon EKS (AWS)

This guide explains how to run **THE NightOps** against **Amazon Elastic Kubernetes Service (EKS)**. No repository code changes are required: the agent uses **`kubectl`** in **Simple mode**, which works with any cluster that exposes the standard Kubernetes API—including EKS.

---

## What you get on AWS

| Capability | Simple mode on EKS |
|------------|-------------------|
| Pod / deployment / event / log investigation via `kubectl` | Yes |
| `nightops agent run --simple` | Yes |
| `nightops agent watch --simple` (webhooks + event watcher + proactive checks) | Yes, if the host can reach the EKS API and your YAML matches your namespaces |
| Google **GKE** MCP or **Cloud Logging** MCP (full multi-agent mode) | No — those integrations target **Google Cloud**. Use Simple mode for AWS. |
| Native **CloudWatch Logs** as an MCP tool | Not built into this repo today; Simple mode is **cluster + kubectl** only. |

**Recommendation:** use **`--simple`** for all EKS workloads unless you add your own tooling or extend the project.

---

## Prerequisites

On the machine that runs NightOps (laptop, bastion, or EC2):

- **Python 3.11+**
- **`kubectl`** ([install](https://kubernetes.io/docs/tasks/tools/))
- **AWS CLI v2** ([install](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html))
- A **Gemini API key** for the AI agent: set **`GOOGLE_API_KEY`** (see `config/.env.example`)

IAM permissions must allow the principal (user, role, or instance profile) to authenticate to the cluster. EKS typically uses **`aws eks get-token`** under the hood when you use `kubectl`; ensure your identity can call **`eks:DescribeCluster`** (and is mapped in **`aws-auth`** / EKS access entries for API server access).

---

## 1. Create or select an EKS cluster

Use any supported path:

- **AWS Console:** [Amazon EKS](https://console.aws.amazon.com/eks/) → Create cluster → node group or Fargate profile.
- **AWS CLI / CloudFormation / Terraform / eksctl:** follow your organization’s standard.

Note the **cluster name** and **Region** (for example `us-east-1`).

Official references:

- [Getting started with Amazon EKS](https://docs.aws.amazon.com/eks/latest/userguide/getting-started.html)
- [Creating an Amazon EKS cluster](https://docs.aws.amazon.com/eks/latest/userguide/create-cluster.html)

---

## 2. Configure kubeconfig for EKS

From a shell where AWS credentials are valid:

```bash
aws eks update-kubeconfig --region <AWS_REGION> --name <CLUSTER_NAME>
```

Optional: use a dedicated kubeconfig file:

```bash
export KUBECONFIG="${HOME}/.kube/eks-<CLUSTER_NAME>"
aws eks update-kubeconfig --region <AWS_REGION> --name <CLUSTER_NAME> --kubeconfig "$KUBECONFIG"
```

Verify:

```bash
kubectl config current-context
kubectl get nodes
kubectl get ns
```

If these fail, fix **AWS auth**, **EKS access entries / aws-auth**, or **network path to the API endpoint** before running NightOps.

---

## 3. Network considerations

- **Public endpoint:** simplest for development; ensure security groups and IAM are correct.
- **Private endpoint only:** the host running `kubectl` (and NightOps) must have **network reachability** to the EKS API (same VPC, VPN, Direct Connect, Transit Gateway, etc.).

---

## 4. Install and configure NightOps

From the repository root (or install the `nightops` package in your environment):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Copy and edit configuration:

```bash
cp config/.env.example config/.env
# Set at least GOOGLE_API_KEY; add other secrets as needed.
```

Create an EKS-oriented copy of the main config (or edit `config/nightops.yaml` in place). For **AWS / kubectl-only** operation, **disable** the official GCP MCP servers so `nightops verify` and your mental model stay aligned with what you actually use:

```yaml
# In mcp_servers (names match config/nightops.yaml → nightops.core.config)
cloud_observability:
  enabled: false

gke:
  enabled: false
```

Leave **custom** `kubernetes` / `cloud_logging` MCP servers **disabled** for Simple mode; the simple agent does not use them—it calls **`kubectl`** directly.

**Namespaces:** set `event_watcher.namespaces` to the namespaces you run on EKS (not only `default` / `nightops-demo`).

Example:

```yaml
event_watcher:
  enabled: true
  namespaces:
    - default
    - production
    - staging
```

---

## 5. Run the agent against EKS

Ensure **`GOOGLE_API_KEY`** is set (or loaded via `config/.env` when using a YAML path that triggers `.env` loading—see `NightOpsConfig.from_yaml`).

```bash
source .venv/bin/activate
export GOOGLE_API_KEY="your-key" 

nightops verify --config config/nightops.yaml

nightops agent run --simple \
  --incident "CrashLoopBackOff in namespace production" \
  --config config/nightops.yaml
```

**Watch mode** (webhook receiver + event watcher + proactive scheduler—still uses your kubeconfig for Kubernetes operations):

```bash
nightops agent watch --simple --config config/nightops.yaml
```

**Dashboard** (optional):

```bash
nightops dashboard --config config/nightops.yaml
```

---

## 6. Running on EC2 (common production pattern)

1. Launch EC2 in a subnet that can reach the **EKS API** (if private endpoint).
2. Attach an **IAM instance profile** (or use **IMDS** with a role) whose identity is granted access to the cluster (EKS access entries / `aws-auth` ConfigMap legacy flow).
3. Install Python 3.11+, `kubectl`, AWS CLI, and NightOps.
4. Run `aws eks update-kubeconfig` on that instance and start `nightops agent watch --simple` as a **systemd** service or under a process manager.

---

## 7. Troubleshooting

| Symptom | Things to check |
|--------|------------------|
| `kubectl get nodes` fails | AWS credentials, region, cluster name, EKS access for your IAM principal |
| NightOps runs but “can’t see” workloads | Current kubectl **context** (`kubectl config current-context`), correct **namespace** in incident text or config |
| `nightops verify` shows GCP checks failing | Expected if you disabled GCP MCP; focus on **kubectl** and **GOOGLE_API_KEY** for Simple mode |
| Webhook / watch mode oddities | **Security groups** (open receiver port from alert sources), **YAML** `webhook.host` / `port`, in-cluster vs out-of-cluster networking |

---

## 8. Summary

- **No code changes** are required for EKS: use **Simple mode** (`--simple`) and a working **kubeconfig**.
- **Turn off** GKE and Cloud Observability MCP in YAML when you are not using Google Cloud.
- You still need **`GOOGLE_API_KEY`** (or equivalent) for Gemini.
- **CloudWatch** and other AWS observability tools are **not** wired in as first-class MCP tools in this repository; extend or integrate separately if you need log correlation beyond `kubectl logs`.

For general NightOps usage, see the [README](../README.md) in the repository root.
