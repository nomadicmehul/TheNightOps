"""
Simple Single-Agent Mode for TheNightOps.

A simplified, reliable agent that uses kubectl subprocess calls instead of MCP.
Works with any Kubernetes cluster that has kubectl configured — no MCP servers,
no IAM setup, no beta features required.

This is the recommended mode for:
- First-time evaluation of TheNightOps
- Environments where GCP MCP is unavailable or unreliable
- Demos and workshops where reliability matters most
- Learning Google ADK patterns without infrastructure complexity

Usage:
    nightops agent run --simple --incident "Pod crashlooping in default namespace"
    nightops agent watch --simple
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from typing import Any

from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from thenightops.core.config import NightOpsConfig

logger = logging.getLogger(__name__)


# ── kubectl-based Tool Functions ─────────────────────────────────
# These are plain Python functions that ADK wraps as tools for the agent.
# No MCP, no SSE, no HTTP — just subprocess calls to kubectl.


def kubectl_get_pods(namespace: str = "default") -> str:
    """Get all pods and their status in a Kubernetes namespace.

    Args:
        namespace: The Kubernetes namespace to query (default: "default").

    Returns:
        Pod information including name, status, restarts, and age.
    """
    return _run_kubectl(
        ["get", "pods", "-n", namespace, "-o", "wide", "--show-labels"]
    )


def kubectl_get_events(namespace: str = "default", sort_by: str = ".lastTimestamp") -> str:
    """Get recent Kubernetes events in a namespace, sorted by time.

    Use this to find warnings, errors, OOMKills, CrashLoopBackOff, and other issues.

    Args:
        namespace: The Kubernetes namespace to query (default: "default").
        sort_by: Field to sort events by (default: ".lastTimestamp").

    Returns:
        Recent events with type, reason, object, and message.
    """
    return _run_kubectl(
        ["get", "events", "-n", namespace, "--sort-by", sort_by]
    )


def kubectl_describe_pod(pod_name: str, namespace: str = "default") -> str:
    """Get detailed description of a specific pod including conditions, events, and container status.

    Args:
        pod_name: Name of the pod (can be a partial name — kubectl will match).
        namespace: The Kubernetes namespace (default: "default").

    Returns:
        Detailed pod description including status, conditions, volumes, and recent events.
    """
    return _run_kubectl(["describe", "pod", pod_name, "-n", namespace])


def kubectl_get_pod_logs(pod_name: str, namespace: str = "default", tail_lines: int = 100, previous: bool = False) -> str:
    """Get logs from a specific pod. Use previous=True to get logs from a crashed container.

    Args:
        pod_name: Name of the pod.
        namespace: The Kubernetes namespace (default: "default").
        tail_lines: Number of recent log lines to retrieve (default: 100).
        previous: If True, get logs from the previous (crashed) container instance.

    Returns:
        Log output from the pod.
    """
    cmd = ["logs", pod_name, "-n", namespace, "--tail", str(tail_lines)]
    if previous:
        cmd.append("--previous")
    return _run_kubectl(cmd)


def kubectl_get_deployments(namespace: str = "default") -> str:
    """Get all deployments and their status in a namespace.

    Args:
        namespace: The Kubernetes namespace to query (default: "default").

    Returns:
        Deployment information including ready replicas, up-to-date, and available counts.
    """
    return _run_kubectl(["get", "deployments", "-n", namespace, "-o", "wide"])


def kubectl_get_deployment_history(deployment_name: str, namespace: str = "default") -> str:
    """Get rollout history of a deployment to see recent changes.

    Args:
        deployment_name: Name of the deployment.
        namespace: The Kubernetes namespace (default: "default").

    Returns:
        Rollout history with revision numbers and change causes.
    """
    return _run_kubectl(
        ["rollout", "history", f"deployment/{deployment_name}", "-n", namespace]
    )


def kubectl_top_pods(namespace: str = "default") -> str:
    """Get current CPU and memory usage for pods in a namespace.

    Requires metrics-server to be installed in the cluster.

    Args:
        namespace: The Kubernetes namespace to query (default: "default").

    Returns:
        CPU and memory usage per pod, or an error if metrics-server is not available.
    """
    return _run_kubectl(["top", "pods", "-n", namespace])


def kubectl_get_nodes() -> str:
    """Get cluster node status and resource information.

    Returns:
        Node information including status, roles, age, version, and OS.
    """
    return _run_kubectl(["get", "nodes", "-o", "wide"])


def kubectl_get_resource_yaml(resource_type: str, resource_name: str, namespace: str = "default") -> str:
    """Get the full YAML definition of a Kubernetes resource.

    Useful for checking resource limits, environment variables, and configuration.

    Args:
        resource_type: Type of resource (e.g., "pod", "deployment", "service").
        resource_name: Name of the resource.
        namespace: The Kubernetes namespace (default: "default").

    Returns:
        Full YAML definition of the resource.
    """
    result = _run_kubectl(
        ["get", resource_type, resource_name, "-n", namespace, "-o", "yaml"]
    )
    # Truncate very long YAML output to avoid overwhelming the model
    if len(result) > 5000:
        return result[:5000] + "\n... (truncated, showing first 5000 chars)"
    return result


def kubectl_get_namespaces() -> str:
    """List all namespaces in the cluster.

    Returns:
        List of namespaces with their status and age.
    """
    return _run_kubectl(["get", "namespaces"])


def _run_kubectl(args: list[str], timeout: int = 30) -> str:
    """Execute a kubectl command and return its output."""
    cmd = ["kubectl"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            error = result.stderr.strip()
            if error:
                return f"Error: {error}"
            return f"Command failed with exit code {result.returncode}"
        output = result.stdout.strip()
        return output if output else "No output returned."
    except subprocess.TimeoutExpired:
        return f"Error: kubectl command timed out after {timeout}s"
    except FileNotFoundError:
        return "Error: kubectl not found. Ensure kubectl is installed and in your PATH."


# ── Simple Agent Instruction ─────────────────────────────────────

_SIMPLE_AGENT_INSTRUCTION = """You are TheNightOps, an autonomous SRE agent that investigates
and helps resolve production Kubernetes incidents.

## Your Tools

You have direct access to kubectl-based tools for investigating Kubernetes clusters:

### Cluster Overview
- `kubectl_get_namespaces` — List all namespaces
- `kubectl_get_nodes` — Get node status and resource info

### Pod Investigation
- `kubectl_get_pods(namespace)` — List pods with status, restarts, age
- `kubectl_describe_pod(pod_name, namespace)` — Detailed pod info including events
- `kubectl_get_pod_logs(pod_name, namespace, tail_lines, previous)` — Get pod logs (use previous=True for crashed pods)
- `kubectl_top_pods(namespace)` — Current CPU/memory usage

### Deployment Investigation
- `kubectl_get_deployments(namespace)` — List deployments with replica status
- `kubectl_get_deployment_history(deployment_name, namespace)` — Rollout history

### Events & Resources
- `kubectl_get_events(namespace)` — Recent K8s events (warnings, errors, OOMKills)
- `kubectl_get_resource_yaml(resource_type, resource_name, namespace)` — Full YAML of any resource

## Investigation Protocol

When you receive an incident, follow this systematic approach:

### Phase 1: Triage
1. Get namespaces to understand the cluster structure
2. Get pods in the relevant namespace(s) — look for CrashLoopBackOff, OOMKilled, Error states
3. Get events to find warnings and errors

### Phase 2: Deep Investigation
Based on Phase 1 findings:
- If pods are crashing → describe the pod, get logs (current AND previous)
- If OOMKill → check resource limits via resource YAML, check memory usage via top pods
- If deployment issue → check rollout history for recent changes
- If unclear → check multiple namespaces, check node health

### Phase 3: Synthesis
Synthesize all findings into a coherent diagnosis:
- Identify the root cause with confidence level
- Trace the chain of events
- Note any correlations between different signals

### Phase 4: Recommendations
- Suggest immediate actions (restart, rollback, scale)
- Flag which actions need human approval
- Recommend long-term fixes

## Output Format

Present your final analysis as:

**Incident Summary**: [one line]
**Severity**: [critical/high/medium/low]
**Root Cause**: [concise explanation]
**Evidence**: [key findings from investigation]
**Impact**: [what's affected and how]
**Immediate Actions**: [what should be done now]
**Long-term Recommendations**: [prevent recurrence]
**Confidence Level**: [low/medium/high with reasoning]

## Important Rules

- Always check events first — they reveal OOMKills, scheduling failures, and health check issues
- For CrashLoopBackOff, always get logs with previous=True to see why the last container crashed
- Don't just report symptoms — trace back to the root cause
- If you can't determine the root cause, say so clearly and list what you've ruled out
- Be concise but thorough in your analysis

## CRITICAL: You MUST always produce a final text response

After completing your investigation, you MUST write out your full analysis in the output format above.
Do NOT end your response with just a tool call — you MUST follow up with your written Root Cause Analysis.
This is the most important part of your job: the written analysis.
"""


# ── Agent Creation ────────────────────────────────────────────────


def create_simple_agent(config: NightOpsConfig) -> Agent:
    """Create a single-agent investigator that uses kubectl tools directly.

    No MCP servers, no sub-agents, no complex orchestration.
    Just one agent with kubectl access and good instructions.
    """
    model = config.agent.model

    # Create FunctionTool wrappers for each kubectl function
    tools = [
        FunctionTool(kubectl_get_pods),
        FunctionTool(kubectl_get_events),
        FunctionTool(kubectl_describe_pod),
        FunctionTool(kubectl_get_pod_logs),
        FunctionTool(kubectl_get_deployments),
        FunctionTool(kubectl_get_deployment_history),
        FunctionTool(kubectl_top_pods),
        FunctionTool(kubectl_get_nodes),
        FunctionTool(kubectl_get_resource_yaml),
        FunctionTool(kubectl_get_namespaces),
    ]

    agent = Agent(
        name="thenightops_simple",
        model=model,
        description=(
            "TheNightOps Simple Mode — Single-agent Kubernetes investigator "
            "using kubectl tools. Reliable, works with any cluster."
        ),
        instruction=_SIMPLE_AGENT_INSTRUCTION,
        tools=tools,
    )

    logger.info(
        "Simple agent created with %d kubectl tools, model=%s",
        len(tools), model,
    )
    return agent


async def run_simple_investigation(
    config: NightOpsConfig,
    incident_description: str,
    dashboard_url: str | None = None,
    incident_id: str | None = None,
) -> dict[str, Any]:
    """Run an investigation using the simple single-agent mode.

    Uses kubectl subprocess calls — no MCP dependency.
    """
    import httpx
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    agent = create_simple_agent(config)

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="thenightops",
        user_id="nightops-system",
    )

    runner = Runner(
        agent=agent,
        app_name="thenightops",
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

    async def _push_event(event_data: dict) -> None:
        if not http_client or not investigation_id_val:
            return
        try:
            event_data["investigation_id"] = investigation_id_val
            resp = await http_client.post("/api/events", json=event_data)
            if resp.status_code != 200:
                logger.warning("Dashboard rejected event (HTTP %d)", resp.status_code)
        except Exception as exc:
            logger.warning("Failed to push event: %s", exc)

    # Build message
    user_message = types.Content(
        role="user",
        parts=[types.Part(text=f"INCIDENT RECEIVED:\n\n{incident_description}")],
    )

    results: list[str] = []
    all_agent_text: list[str] = []  # Capture ALL text from agent for fallback
    all_tool_results: list[dict[str, str]] = []  # Tool name -> output for synthetic RCA
    tools_called = 0
    current_phase = 1

    await _push_event({"type": "phase_changed", "phase": 1})

    try:
        async for event in runner.run_async(
            user_id="nightops-system",
            session_id=session.id,
            new_message=user_message,
        ):
            author = event.author or "thenightops_simple"

            # Track tool calls
            fn_calls = event.get_function_calls()
            if fn_calls:
                for fc in fn_calls:
                    tools_called += 1
                    tool_name = fc.name

                    # Phase inference from tool usage
                    if current_phase == 1 and tools_called > 3:
                        current_phase = 2
                        await _push_event({"type": "phase_changed", "phase": 2})
                    if current_phase == 2 and tools_called > 6:
                        current_phase = 3
                        await _push_event({"type": "phase_changed", "phase": 3})

                    await _push_event({
                        "type": "tool_called",
                        "agent": author,
                        "tool_name": tool_name,
                        "tool_input": str(fc.args)[:500],
                    })

            # Track tool responses as findings
            fn_responses = event.get_function_responses()
            if fn_responses:
                for fr in fn_responses:
                    resp_str = str(fr.response)
                    # Save for synthetic RCA fallback
                    if len(resp_str) > 20:
                        all_tool_results.append({
                            "tool": fr.name,
                            "output": resp_str[:500],
                        })
                        await _push_event({
                            "type": "finding_added",
                            "source_agent": author,
                            "severity": "medium",
                            "description": f"{fr.name}: {resp_str[:200]}",
                        })

            # Capture ALL text from agent events (reasoning + final analysis)
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        text = part.text.strip()
                        if text:
                            all_agent_text.append(text)
                            # Push reasoning to dashboard timeline
                            if not event.is_final_response():
                                await _push_event({
                                    "type": "agent_delegated",
                                    "agent": author,
                                    "task": text[:300],
                                })

            # Final response — capture explicitly
            if event.is_final_response():
                if current_phase < 4:
                    current_phase = 4
                    await _push_event({"type": "phase_changed", "phase": 4})
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            results.append(part.text)

    except BaseException as exc:
        detail = str(exc)
        logger.error("Simple investigation failed: %s", detail)

        if http_client:
            try:
                await http_client.aclose()
            except Exception:
                pass

        # Notify dashboard of failure with fresh client
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
            except Exception:
                pass

        raise RuntimeError(f"Investigation failed: {detail}") from exc

    investigation_result = "\n".join(results)

    # Fallback 1: if is_final_response() didn't capture text, use the longest
    # agent text block (which is typically the final analysis/RCA)
    if not investigation_result.strip() and all_agent_text:
        # Find the longest text block — that's most likely the RCA summary
        longest = max(all_agent_text, key=len)
        if len(longest) > 100:
            investigation_result = longest
            logger.info(
                "Used fallback 1: captured RCA from agent text (%d chars, %d total blocks)",
                len(longest), len(all_agent_text),
            )
        else:
            # Concatenate all text as last resort
            investigation_result = "\n\n".join(all_agent_text)

    # Fallback 2: if still empty, synthesize a summary from tool results
    if not investigation_result.strip() and all_tool_results:
        logger.info(
            "Used fallback 2: synthesizing RCA from %d tool results", len(all_tool_results),
        )
        lines = [
            f"**Incident Summary**: {incident_description}",
            f"**Investigation Mode**: Automated (Simple Mode — {tools_called} tools called)",
            "",
            "**Evidence Collected**:",
        ]
        for tr in all_tool_results:
            lines.append(f"- `{tr['tool']}`: {tr['output'][:200]}")
        lines.extend([
            "",
            "**Note**: The AI model completed tool calls but did not generate a written "
            "analysis. The raw evidence above was collected during the investigation. "
            "Review the tool outputs to determine the root cause.",
        ])
        investigation_result = "\n".join(lines)

    if not investigation_result.strip():
        investigation_result = (
            "Investigation completed but produced no analysis. "
            "The model may not have generated a final response."
        )
        status = "failed"
    else:
        status = "completed"

    await _push_event({
        "type": "investigation_completed",
        "status": status,
        "rca_summary": investigation_result[:2000],
    })

    if http_client:
        await http_client.aclose()

    logger.info(
        "Simple investigation complete: %d tools called, status=%s",
        tools_called, status,
    )

    return {
        "session_id": session.id,
        "result": investigation_result,
        "tools_called": tools_called,
        "status": status,
    }
