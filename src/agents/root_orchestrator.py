"""
Root Orchestrator Agent for TheNightOps.

The main agent that receives incidents and coordinates investigation
across all sub-agents using Google ADK's multi-agent orchestration.

Uses custom MCP servers (SSE transport) for:
- Kubernetes: pod status, logs, events, deployments, resource usage
- Cloud Logging: log queries, error pattern detection, anomaly detection
- Slack: incident notifications (optional)
- Notifications: Email, Telegram, WhatsApp (optional)

Enhanced with:
- Anomaly Detector sub-agent for proactive monitoring
- Incident Memory for pattern matching against historical incidents
- Graduated Remediation for safe auto-actions
- Impact Metrics for tracking effectiveness
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import google.auth
import google.auth.transport.requests
from google.adk.agents import Agent
from google.adk.tools.mcp_tool import McpToolset, SseConnectionParams, StdioConnectionParams, StreamableHTTPConnectionParams
from mcp import StdioServerParameters

from nightops.agents.communication_drafter import create_communication_drafter_agent
from nightops.agents.deployment_correlator import create_deployment_correlator_agent
from nightops.agents.log_analyst import create_log_analyst_agent
from nightops.agents.runbook_retriever import create_runbook_retriever_agent
from nightops.agents.anomaly_detector import create_anomaly_detector_agent
from nightops.core.config import NightOpsConfig

logger = logging.getLogger(__name__)


def _create_gcp_header_provider(
    project_id: str,
    extra_headers: dict[str, str] | None = None,
):
    """Create a dynamic header provider that injects fresh GCP OAuth2 tokens.

    Official Google Cloud MCP servers require IAM authentication via bearer tokens.
    Tokens expire (~1 hour), so this provider refreshes them on each request.

    Requires: gcloud auth application-default login (or Workload Identity on GKE)
    """
    def provider(context) -> dict[str, str]:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(google.auth.transport.requests.Request())
        headers = {
            "Authorization": f"Bearer {credentials.token}",
            "x-goog-project-id": project_id,
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    return provider

_ROOT_ORCHESTRATOR_BASE = """You are TheNightOps, an autonomous SRE agent that investigates
and helps resolve production incidents. You coordinate a team of specialist agents.

## CRITICAL: Only Use Available Tools

You MUST ONLY call tools that are actually registered and available to you.
Do NOT invent or guess tool names. If a tool call fails, do NOT retry with the same name.

{tool_section}

### Agent Transfer
- `transfer_to_agent` — Delegate work to a sub-agent

## Your Sub-Agents

You have five specialist agents you can delegate to:

1. **log_analyst** — Analyses logs for error patterns, anomalies, and trace correlation
2. **deployment_correlator** — Checks Kubernetes for recent deployments, pod health, events, and resource exhaustion
3. **runbook_retriever** — Searches for alert history, past incidents, and similar patterns
4. **communication_drafter** — Generates RCAs and stakeholder notifications via Slack, Email, Telegram, WhatsApp
5. **anomaly_detector** — Proactively monitors cluster health by checking pod status, memory trends, error rates

## Historical Context

Before investigating, check if this incident matches a known pattern. If the system
provides similar historical incidents, use that context to accelerate diagnosis:
- If a similar incident was resolved before, check if the same root cause applies
- If the pattern recurs, flag it as a recurring issue and recommend permanent fixes
- Use historical MTTR as a benchmark for this investigation

## Investigation Protocol

When you receive an incident, follow this systematic approach:

### Phase 1: Initial Triage (Parallel)
{triage_section}

### Phase 2: Deep Investigation
{deep_investigation_section}

### Phase 3: Synthesis & Communication
Once you have sufficient evidence:
1. Synthesise all findings into a coherent diagnosis
2. Determine root cause with confidence level
3. Present your findings in the output format below — do NOT attempt to call notification tools

### Phase 4: Remediation Recommendations
Based on your analysis:
- Suggest immediate actions (e.g., rollback deployment, restart pods)
- Flag which actions can be auto-approved vs require human approval
- Recommend long-term fixes to prevent recurrence

## Remediation Policy

Actions are governed by graduated autonomy:
- **Auto-approved**: Creating incidents, posting to #incidents
- **Environment-gated**: Pod restarts (auto in dev/staging, approval needed in prod)
- **Always require approval**: Rollbacks, scaling changes, external notifications
- **Blocked**: Namespace deletion, PVC deletion, cluster operations

## Decision Framework

- **High confidence root cause found**: Proceed to Phase 3 immediately
- **Multiple possible causes**: Investigate the most likely one first, note alternatives
- **No clear root cause after Phase 2**: Summarise what you've found, escalate to human
- **Recurring incident**: Highlight this pattern and recommend permanent fix
- **Proactive detection**: If anomaly_detector found this, note it was caught proactively

## Human-in-the-Loop

You MUST request human approval before:
- Restarting pods or services in production
- Rolling back deployments
- Sending external stakeholder notifications
- Any action that modifies the production environment

## Output Format

Present your final analysis as:

**Incident Summary**: [one line]
**Severity**: [critical/high/medium/low]
**Root Cause**: [concise explanation]
**Evidence**: [key findings from investigation]
**Impact**: [what's affected and how]
**Immediate Actions**: [what should be done now — mark auto-approved vs needs approval]
**Long-term Recommendations**: [prevent recurrence]
**Confidence Level**: [low/medium/high with reasoning]
**Historical Match**: [similar past incident if found, or "No matching historical pattern"]
"""

_GCP_TOOL_SECTION = """### GKE MCP Tools (Official Google Cloud)
- `kube_get` — Get any Kubernetes resource (pods, deployments, events, services, etc.)
  Usage: specify the resource kind (e.g. "pods", "deployments", "events"), namespace, and optionally a name.
- `kube_api_resources` — List available Kubernetes API resource types
- `list_clusters` — List GKE clusters
- `get_cluster` — Get GKE cluster details
- `list_node_pools` — List node pools in a cluster
- `get_node_pool` — Get node pool details

### Cloud Observability MCP Tools (Official Google Cloud)
- `list_log_entries` — Query and list Cloud Logging entries with filter expressions
  Usage: provide a filter string (e.g. 'resource.type="k8s_container" severity>=ERROR') and time range.
- `list_log_names` — List available log names in the project"""

_LOCAL_TOOL_SECTION = """### Kubernetes MCP Tools (Custom)
- `get_pod_status` — Get pod status in a namespace
- `get_pod_logs` — Get logs from a specific pod
- `get_events` — Get Kubernetes events in a namespace
- `get_deployments` — List deployments in a namespace
- `get_resource_usage` — Get resource usage for pods
- `describe_pod` — Get detailed pod description

### Cloud Logging MCP Tools (Custom)
- `query_logs` — Query Cloud Logging for log entries
- `detect_error_patterns` — Detect error patterns in logs
- `get_log_volume_anomalies` — Find log volume anomalies
- `correlate_logs_by_trace` — Correlate logs by trace ID"""

_GCP_TRIAGE_SECTION = """Use these tools directly:
- `kube_get` with kind "pods": Check pod health across relevant namespaces
- `kube_get` with kind "events": Get recent Kubernetes events for anomalies
- `kube_get` with kind "deployments": Check recent deployment changes
- `list_log_entries` with severity filter: Search for error patterns in logs"""

_LOCAL_TRIAGE_SECTION = """Use these tools directly:
- `get_pod_status`: Check pod health across relevant namespaces
- `get_events`: Get recent Kubernetes events for anomalies
- `get_deployments`: Check recent deployment changes
- `query_logs`: Search for error patterns in logs
- `detect_error_patterns`: Find error patterns automatically"""

_GCP_DEEP_INVESTIGATION_SECTION = """Based on Phase 1 findings, perform targeted follow-up:
- If deployment change detected → `kube_get` with kind "pods" + name to inspect specific pods
- If error patterns found → `list_log_entries` with trace filter to trace the issue
- If resource issues → `kube_get` with kind "pods" to check resource requests/limits
- If OOMKill → `kube_get` kind "events" + `list_log_entries` to confirm and find the cause"""

_LOCAL_DEEP_INVESTIGATION_SECTION = """Based on Phase 1 findings, perform targeted follow-up:
- If deployment change detected → `describe_pod`, `get_pod_logs` on specific pods
- If error patterns found → `correlate_logs_by_trace` to trace the issue
- If resource issues → `get_resource_usage` to check CPU/memory
- If OOMKill → `get_pod_logs` + `get_events` to confirm and find the cause"""


def _build_root_instruction(use_gcp: bool) -> str:
    """Build root orchestrator instruction based on MCP mode."""
    return _ROOT_ORCHESTRATOR_BASE.format(
        tool_section=_GCP_TOOL_SECTION if use_gcp else _LOCAL_TOOL_SECTION,
        triage_section=_GCP_TRIAGE_SECTION if use_gcp else _LOCAL_TRIAGE_SECTION,
        deep_investigation_section=(
            _GCP_DEEP_INVESTIGATION_SECTION if use_gcp else _LOCAL_DEEP_INVESTIGATION_SECTION
        ),
    )


def create_mcp_toolsets(config: NightOpsConfig) -> list[McpToolset]:
    """Create MCP toolset connections for all configured servers.

    Connects to custom MCP servers via SSE transport.
    Also supports official Google Cloud MCP and Grafana MCP when enabled.
    """
    toolsets = []

    # ── Official Google Cloud MCP Servers (IAM-authenticated, Streamable HTTP) ──
    if config.cloud_observability.enabled:
        logger.info(
            "Connecting to Cloud Observability MCP: %s (project: %s)",
            config.cloud_observability.endpoint,
            config.cloud_observability.project_id,
        )
        toolsets.append(
            McpToolset(
                connection_params=StreamableHTTPConnectionParams(
                    url=config.cloud_observability.endpoint,
                ),
                header_provider=_create_gcp_header_provider(
                    config.cloud_observability.project_id,
                ),
            )
        )

    if config.gke.enabled:
        logger.info(
            "Connecting to official GKE MCP: %s (cluster: %s/%s)",
            config.gke.endpoint,
            config.gke.location,
            config.gke.cluster,
        )
        toolsets.append(
            McpToolset(
                connection_params=StreamableHTTPConnectionParams(
                    url=config.gke.endpoint,
                ),
                header_provider=_create_gcp_header_provider(
                    config.gke.project_id,
                    extra_headers={
                        "x-gke-cluster": config.gke.cluster,
                        "x-gke-location": config.gke.location,
                    },
                ),
            )
        )

    # ── Multi-Cluster GKE MCP connections ─────────────────────────
    for cluster_config in config.clusters:
        if not cluster_config.enabled:
            continue
        logger.info(
            "Connecting to GKE MCP for cluster: %s/%s (%s)",
            cluster_config.location, cluster_config.name, cluster_config.environment,
        )
        toolsets.append(
            McpToolset(
                connection_params=StreamableHTTPConnectionParams(
                    url=cluster_config.gke_mcp_endpoint,
                ),
                header_provider=_create_gcp_header_provider(
                    cluster_config.project_id,
                    extra_headers={
                        "x-gke-cluster": cluster_config.name,
                        "x-gke-location": cluster_config.location,
                    },
                ),
            )
        )

    # ── Grafana MCP (stdio transport) ────────────────────────────
    if config.grafana.enabled:
        logger.info(
            "Connecting to Grafana MCP: %s (stdio via uvx mcp-grafana)",
            config.grafana.url,
        )
        grafana_args = ["mcp-grafana"]
        if config.grafana.enabled_tools:
            grafana_args.extend(["--enabled-tools", config.grafana.enabled_tools])

        toolsets.append(
            McpToolset(
                connection_params=StdioConnectionParams(
                    server_params=StdioServerParameters(
                        command="uvx",
                        args=grafana_args,
                        env={
                            "GRAFANA_URL": config.grafana.url,
                            "GRAFANA_SERVICE_ACCOUNT_TOKEN": config.grafana.service_account_token or "",
                        },
                    ),
                ),
            )
        )

    # ── Custom MCP Servers (self-hosted via SSE) ────────────────

    if config.kubernetes.enabled:
        logger.info(
            "Connecting to custom Kubernetes MCP: %s:%d",
            config.kubernetes.host,
            config.kubernetes.port,
        )
        toolsets.append(
            McpToolset(
                connection_params=SseConnectionParams(
                    url=f"http://{config.kubernetes.host}:{config.kubernetes.port}/sse",
                ),
            )
        )

    if config.cloud_logging_custom.enabled:
        logger.info(
            "Connecting to custom Cloud Logging MCP: %s:%d",
            config.cloud_logging_custom.host,
            config.cloud_logging_custom.port,
        )
        toolsets.append(
            McpToolset(
                connection_params=SseConnectionParams(
                    url=f"http://{config.cloud_logging_custom.host}:{config.cloud_logging_custom.port}/sse",
                ),
            )
        )

    if config.slack.enabled:
        logger.info(
            "Connecting to custom Slack MCP: %s:%d",
            config.slack.host,
            config.slack.port,
        )
        toolsets.append(
            McpToolset(
                connection_params=SseConnectionParams(
                    url=f"http://{config.slack.host}:{config.slack.port}/sse",
                ),
            )
        )

    if config.notifications.enabled:
        logger.info(
            "Connecting to custom Notifications MCP: %s:%d (Email/Telegram/WhatsApp)",
            config.notifications.host,
            config.notifications.port,
        )
        toolsets.append(
            McpToolset(
                connection_params=SseConnectionParams(
                    url=f"http://{config.notifications.host}:{config.notifications.port}/sse",
                ),
            )
        )

    return toolsets


def _is_gcp_mode(config: NightOpsConfig) -> bool:
    """Determine if we're using official GCP MCP servers (vs custom/local)."""
    return config.gke.enabled or config.cloud_observability.enabled


def create_root_orchestrator(
    config: NightOpsConfig,
) -> Agent:
    """
    Create the root orchestrator agent with all sub-agents and MCP toolsets.

    Architecture:
    - Root Orchestrator coordinates 5 specialist sub-agents
    - Sub-agents access tools via custom MCP servers (SSE transport)
    - All powered by Google ADK's multi-agent orchestration + Gemini
    """
    model = config.agent.model
    use_gcp = _is_gcp_mode(config)

    if use_gcp:
        logger.info("Using GCP mode — agent instructions will reference official MCP tool names")
    else:
        logger.info("Using local mode — agent instructions will reference custom MCP tool names")

    # Create MCP toolsets first (so sub-agents can use them)
    mcp_toolsets = create_mcp_toolsets(config)

    # Create sub-agents — give investigation agents access to MCP tools
    # Communication drafter has no tools (text output only)
    log_analyst = create_log_analyst_agent(model=model, tools=list(mcp_toolsets), use_gcp=use_gcp)
    deployment_correlator = create_deployment_correlator_agent(
        model=model, tools=list(mcp_toolsets), use_gcp=use_gcp,
    )
    runbook_retriever = create_runbook_retriever_agent(
        model=model, tools=list(mcp_toolsets), use_gcp=use_gcp,
    )
    communication_drafter = create_communication_drafter_agent(model=model)
    anomaly_detector = create_anomaly_detector_agent(
        model=model, tools=list(mcp_toolsets), use_gcp=use_gcp,
    )

    # Build the root orchestrator
    root_agent = Agent(
        name="nightops",
        model=model,
        description=(
            "TheNightOps — Autonomous SRE agent that investigates production "
            "incidents by coordinating log analysis, deployment correlation, "
            "proactive anomaly detection, and stakeholder communication "
            "using Kubernetes and Cloud Logging MCP servers."
        ),
        instruction=_build_root_instruction(use_gcp),
        sub_agents=[
            log_analyst,
            deployment_correlator,
            runbook_retriever,
            communication_drafter,
            anomaly_detector,
        ],
        tools=[*mcp_toolsets],
    )

    toolset_count = len(mcp_toolsets)
    cluster_count = sum(1 for c in config.clusters if c.enabled)

    logger.info(
        "Root orchestrator created with %d sub-agents, %d MCP toolsets, %d extra clusters",
        5,
        toolset_count,
        cluster_count,
    )

    return root_agent


async def run_investigation(
    config: NightOpsConfig,
    incident_description: str,
    dashboard_url: str | None = None,
    incident_id: str | None = None,
) -> dict[str, Any]:
    """
    Run a full incident investigation.

    Enhanced with:
    - Incident Memory: Finds similar historical incidents to accelerate diagnosis
    - Metrics Tracking: Records investigation metrics for impact reporting
    - Remediation Engine: Suggests auto-approvable vs manual actions
    """
    import httpx
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    # ── Intelligence Layer: Find similar historical incidents ────
    historical_context = ""
    if config.intelligence.enabled:
        try:
            from nightops.intelligence.incident_memory import IncidentMemory
            memory = IncidentMemory(config.intelligence)
            similar = memory.find_similar(incident_description)
            if similar:
                historical_context = "\n\n## Historical Context (Similar Past Incidents)\n"
                for s in similar:
                    historical_context += (
                        f"\n- **{s.title}** (similarity: {s.similarity_score:.0%})\n"
                        f"  Root cause: {s.root_cause}\n"
                        f"  Resolution: {s.resolution}\n"
                        f"  MTTR: {s.mttr_seconds:.0f}s\n"
                    )
                logger.info("Found %d similar historical incidents", len(similar))
        except Exception:
            logger.debug("Incident memory not available, proceeding without history")

    # ── Remediation Policy Context ────────────────────────────────
    remediation_context = ""
    if config.remediation.enabled:
        try:
            from nightops.remediation.policy_engine import PolicyEngine
            engine = PolicyEngine(config.remediation.policy_path)
            summary = engine.get_policy_summary()
            remediation_context = "\n\n## Remediation Policies\n"
            for action, status in summary.items():
                remediation_context += f"- {action}: {status}\n"
        except Exception:
            logger.debug("Remediation policies not available")

    # ── Validate MCP configuration early ─────────────────────────
    config_errors = []
    if config.cloud_observability.enabled and not config.cloud_observability.project_id:
        config_errors.append("Cloud Observability MCP enabled but project_id is empty")
    if config.gke.enabled:
        if not config.gke.project_id:
            config_errors.append("GKE MCP enabled but project_id is empty")
        if not config.gke.cluster:
            config_errors.append("GKE MCP enabled but cluster name is empty")
        if not config.gke.location:
            config_errors.append("GKE MCP enabled but location is empty")
    if config_errors:
        error_msg = "MCP config invalid: " + "; ".join(config_errors)
        logger.error(error_msg)
        raise ValueError(error_msg)

    agent = create_root_orchestrator(config)

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="nightops",
        user_id="nightops-system",
    )

    runner = Runner(
        agent=agent,
        app_name="nightops",
        session_service=session_service,
    )

    # ── Dashboard integration ────────────────────────────────
    investigation_id_val: str | None = None
    http_client: httpx.AsyncClient | None = None

    if dashboard_url:
        http_client = httpx.AsyncClient(base_url=dashboard_url, timeout=5.0)
        try:
            resp = await http_client.post(
                "/api/investigations",
                params={
                    "incident_description": incident_description,
                    "severity": "medium",
                },
            )
            if resp.status_code == 200:
                investigation_id_val = resp.json().get("id")
                logger.info("Dashboard investigation created: %s", investigation_id_val)
        except Exception as exc:
            logger.warning("Could not register investigation on dashboard: %s", exc)

    async def _push_dashboard_event(event_data: dict) -> None:
        if not http_client or not investigation_id_val:
            return
        try:
            event_data["investigation_id"] = investigation_id_val
            resp = await http_client.post("/api/events", json=event_data)
            if resp.status_code != 200:
                logger.warning(
                    "Dashboard rejected event (HTTP %d): %s",
                    resp.status_code, event_data.get("type"),
                )
        except Exception as exc:
            logger.warning("Failed to push %s event to dashboard: %s",
                           event_data.get("type", "?"), exc)

    # Build enriched incident message with historical context
    enriched_message = f"INCIDENT RECEIVED:\n\n{incident_description}"
    if historical_context:
        enriched_message += historical_context
    if remediation_context:
        enriched_message += remediation_context

    user_message = types.Content(
        role="user",
        parts=[types.Part(text=enriched_message)],
    )

    results: list[str] = []
    all_agent_text: list[str] = []  # Capture ALL text for fallback
    tools_called = 0
    current_phase = 1
    # Track which sub-agents have been seen to infer phase transitions
    # Phase 1 (Triage): root agent calls tools directly
    # Phase 2 (Deep Investigation): sub-agents are delegated to
    # Phase 3 (Synthesis): root agent produces text after sub-agents finish
    # Phase 4 (Remediation): final response with recommendations
    sub_agents_seen: set[str] = set()
    sub_agent_names = {"log_analyst", "deployment_correlator", "runbook_retriever",
                       "anomaly_detector", "communication_drafter"}

    await _push_dashboard_event({"type": "phase_changed", "phase": 1})

    try:
        async for event in runner.run_async(
            user_id="nightops-system",
            session_id=session.id,
            new_message=user_message,
        ):
            author = event.author or "nightops"

            # Phase inference: detect transitions based on agent activity
            if current_phase == 1 and author in sub_agent_names:
                # First sub-agent delegation → transition to Phase 2
                current_phase = 2
                await _push_dashboard_event({"type": "phase_changed", "phase": 2})

            if author in sub_agent_names:
                sub_agents_seen.add(author)

            if (current_phase == 2
                    and author == "nightops"
                    and sub_agents_seen
                    and event.content and event.content.parts):
                # Root agent producing text after sub-agents → Phase 3 (Synthesis)
                has_text = any(
                    hasattr(p, "text") and p.text for p in event.content.parts
                )
                if has_text and not event.is_final_response():
                    current_phase = 3
                    await _push_dashboard_event({"type": "phase_changed", "phase": 3})

            fn_calls = event.get_function_calls()
            if fn_calls:
                for fc in fn_calls:
                    tools_called += 1
                    await _push_dashboard_event({
                        "type": "tool_called",
                        "agent": author,
                        "tool_name": fc.name,
                        "tool_input": str(fc.args)[:500],
                    })

            fn_responses = event.get_function_responses()
            if fn_responses:
                for fr in fn_responses:
                    await _push_dashboard_event({
                        "type": "finding_added",
                        "source_agent": author,
                        "severity": "medium",
                        "description": f"Tool {fr.name} returned data ({len(str(fr.response))} chars)",
                    })

            if event.content and event.content.parts:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        all_agent_text.append(part.text.strip())
                        if not event.is_final_response():
                            await _push_dashboard_event({
                                "type": "agent_delegated",
                                "agent": author,
                                "task": part.text[:300],
                            })

            if event.is_final_response():
                # Final response → Phase 4 (Remediation/Complete)
                if current_phase < 4:
                    current_phase = 4
                    await _push_dashboard_event({"type": "phase_changed", "phase": 4})
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            results.append(part.text)
    except BaseException as exc:
        # Extract real errors from ExceptionGroup (TaskGroup failures)
        sub_errors = []
        if hasattr(exc, "exceptions"):
            for sub_exc in exc.exceptions:
                sub_errors.append(f"{type(sub_exc).__name__}: {sub_exc}")
                logger.error("MCP sub-exception: %s: %s", type(sub_exc).__name__, sub_exc)
        detail = "; ".join(sub_errors) if sub_errors else str(exc)
        logger.error("Investigation failed: %s", detail)

        # Close the existing (possibly broken) client before notifying dashboard
        if http_client:
            try:
                await http_client.aclose()
            except Exception:
                pass

        # Use a FRESH httpx client to notify dashboard of failure.
        # The original client's connection pool is likely broken after the
        # TaskGroup ExceptionGroup, so reusing it silently fails.
        if dashboard_url and investigation_id_val:
            try:
                async with httpx.AsyncClient(
                    base_url=dashboard_url, timeout=5.0,
                ) as fresh_client:
                    await fresh_client.post("/api/events", json={
                        "investigation_id": investigation_id_val,
                        "type": "investigation_completed",
                        "status": "failed",
                        "rca_summary": f"Investigation failed: {detail}",
                    })
                    logger.info("Dashboard notified of investigation failure")
            except Exception as notify_exc:
                logger.warning(
                    "Could not notify dashboard of failure: %s", notify_exc,
                )

        raise RuntimeError(f"Investigation failed: {detail}") from exc

    investigation_result = "\n".join(results)

    # Fallback: if is_final_response() didn't capture text, use agent text
    if not investigation_result.strip() and all_agent_text:
        longest = max(all_agent_text, key=len)
        if len(longest) > 100:
            investigation_result = longest
            logger.info("Used fallback: captured RCA from agent text (%d chars)", len(longest))
        else:
            investigation_result = "\n\n".join(all_agent_text)

    # If still empty, mark as failed
    if not investigation_result.strip():
        logger.warning("Investigation produced no results (runner completed with empty output)")
        investigation_result = (
            "Investigation completed but produced no analysis. "
            "This may indicate MCP tool responses were empty or the model "
            "did not generate a final response."
        )
        status = "failed"
    else:
        status = "completed"

    await _push_dashboard_event({
        "type": "investigation_completed",
        "status": status,
        "rca_summary": investigation_result[:2000],
    })

    if http_client:
        await http_client.aclose()

    return {
        "session_id": session.id,
        "incident_id": incident_id,
        "investigation_id": investigation_id_val,
        "investigation_result": investigation_result,
        "tools_called": tools_called,
        "historical_matches": len(historical_context.split("\n- ")) - 1 if historical_context else 0,
    }
