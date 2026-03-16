# TheNightOps — Architecture Deep Dive & Learning Guide

> A comprehensive breakdown of how TheNightOps works internally — covering Plan A (Full MCP Multi-Agent), Plan B (Simple kubectl Single-Agent), how ADK orchestrates everything, and exactly where Gemini gets called.
>
> **Last Updated:** 2026-03-16

---

## Table of Contents

1. [High-Level Overview](#1-high-level-overview)
2. [Plan A — Full MCP Multi-Agent Mode](#2-plan-a--full-mcp-multi-agent-mode)
3. [Plan B — Simple kubectl Single-Agent Mode](#3-plan-b--simple-kubectl-single-agent-mode)
4. [How Google ADK Works](#4-how-google-adk-works)
5. [Where & How Gemini Gets Called](#5-where--how-gemini-gets-called)
6. [Function Call Chain — Plan A](#6-function-call-chain--plan-a)
7. [Function Call Chain — Plan B](#7-function-call-chain--plan-b)
8. [The ADK Runner Loop (Internal)](#8-the-adk-runner-loop-internal)
9. [Agent Files — Quick Reference](#9-agent-files--quick-reference)
10. [Sub-Agent Details](#10-sub-agent-details)
11. [MCP Servers & Tool Mapping](#11-mcp-servers--tool-mapping)
12. [Key Patterns to Remember](#12-key-patterns-to-remember)
13. [File Navigation Map](#13-file-navigation-map)

---

## 1. High-Level Overview

TheNightOps is an **Autonomous SRE Agent** that investigates Kubernetes incidents using:
- **Google ADK** (Agent Development Kit) — for agent orchestration
- **Gemini LLM** — the "brain" that decides what to investigate and how to interpret results
- **MCP** (Model Context Protocol) — for connecting to external tools (K8s, Cloud Logging, etc.)

There are **two operational modes**:

| | Plan A (Full MCP) | Plan B (Simple) |
|---|---|---|
| **Agents** | 1 root + 5 sub-agents | 1 single agent |
| **Tools** | MCP servers (HTTP/SSE) | kubectl subprocess calls |
| **Infra needed** | GCP project + IAM + MCP servers | Just kubectl access |
| **CLI flag** | `nightops agent run` (default) | `nightops agent run --simple` |
| **Entry function** | `run_investigation()` | `run_simple_investigation()` |
| **Source file** | `src/agents/root_orchestrator.py` | `src/agents/simple_agent.py` |
| **Gemini calls** | ~10-20 (across all agents) | ~5-10 (one agent) |
| **Best for** | Production, full GCP setup | Demos, workshops, quick eval |

---

## 2. Plan A — Full MCP Multi-Agent Mode

### Architecture Diagram

```
                         ┌───────────────────────────┐
                         │      CLI / Webhook         │
                         │  nightops agent run        │
                         │  --incident "Pod crash..." │
                         └────────────┬──────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────────┐
                    │      Root Orchestrator Agent         │
                    │  (thenightops)                       │
                    │  model: gemini-3.1-pro-preview       │
                    │  file: root_orchestrator.py          │
                    │                                      │
                    │  Has: MCP tools + 5 sub-agents       │
                    │  Can: call tools directly OR         │
                    │       delegate to sub-agents         │
                    └──────┬──┬──┬──┬──┬─────────────────┘
                           │  │  │  │  │
              ┌────────────┘  │  │  │  └────────────┐
              ▼               ▼  │  ▼               ▼
    ┌──────────────┐ ┌─────────┐│┌──────────┐ ┌──────────────┐
    │ Log Analyst  │ │Deploymnt│││ Runbook  │ │  Anomaly     │
    │              │ │Correlator│ │Retriever │ │  Detector    │
    │ log_analyst  │ │         │││          │ │              │
    │ .py          │ │deploymnt││ │runbook_  │ │anomaly_      │
    │              │ │_corr.py │││retriever │ │detector.py   │
    │ Tools: MCP   │ │Tools:MCP│││.py       │ │Tools: MCP    │
    └──────────────┘ └─────────┘││Tools:MCP │ └──────────────┘
                                ▼└──────────┘
                      ┌──────────────┐
                      │Communication │
                      │  Drafter     │
                      │              │
                      │comm_drafter  │
                      │.py           │
                      │              │
                      │Tools: NONE   │
                      │(text only)   │
                      └──────────────┘
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
          ┌─────────────────┐    ┌─────────────────────┐
          │  GKE MCP Server │    │ Cloud Observability  │
          │  (Official GCP) │    │ MCP Server (GCP)     │
          │                 │    │                      │
          │ container.      │    │ logging.             │
          │ googleapis.com  │    │ googleapis.com       │
          │ /mcp            │    │ /mcp                 │
          │                 │    │                      │
          │ Tools:          │    │ Tools:               │
          │ • kube_get      │    │ • list_log_entries   │
          │ • list_clusters │    │ • list_log_names     │
          │ • get_cluster   │    │                      │
          │ • list_node_    │    └─────────────────────┘
          │   pools         │
          └─────────────────┘

          Optional (if enabled):
          ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
          │ Grafana MCP  │  │ Slack MCP    │  │Notifications │
          │ (stdio)      │  │ (SSE)        │  │MCP (SSE)     │
          │ Port: stdio  │  │ Port: 8003   │  │Port: 8004    │
          └──────────────┘  └──────────────┘  └──────────────┘
```

### How Delegation Works (Plan A Only)

ADK provides an automatic `transfer_to_agent` tool when sub-agents are defined. The flow is:

```
Root Orchestrator (Gemini) → "I need to analyze logs"
  → function_call: transfer_to_agent(agent_name="log_analyst")

ADK INTERNALLY:
  → Pauses root orchestrator
  → Starts NEW Gemini conversation with log_analyst's instruction
  → log_analyst calls its MCP tools (list_log_entries, etc.)
  → log_analyst produces findings text
  → ADK returns findings to root orchestrator

Root Orchestrator (Gemini) → "Now I have log findings, let me check deployments"
  → function_call: transfer_to_agent(agent_name="deployment_correlator")
  → ... same pattern ...
```

**Key insight**: Each sub-agent gets its OWN Gemini conversation with its own specialized instruction. The root agent decides WHEN to delegate based on its investigation protocol.

---

## 3. Plan B — Simple kubectl Single-Agent Mode

### Architecture Diagram

```
                         ┌───────────────────────────────┐
                         │      CLI / Webhook             │
                         │  nightops agent run --simple   │
                         │  --incident "Pod crash..."     │
                         └────────────┬──────────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────────┐
                    │     Simple Agent                     │
                    │  (thenightops_simple)                │
                    │  model: gemini-3.1-pro-preview       │
                    │  file: simple_agent.py               │
                    │                                      │
                    │  Has: 10 kubectl FunctionTools       │
                    │  No sub-agents, no MCP               │
                    └──────────┬──────────────────────────┘
                               │
                               │  Each tool = subprocess.run(["kubectl", ...])
                               │
               ┌───────────────┼───────────────────┐
               ▼               ▼                   ▼
    ┌───────────────┐ ┌──────────────┐  ┌──────────────────┐
    │kubectl_get_   │ │kubectl_      │  │kubectl_get_      │
    │pods()         │ │describe_pod()│  │resource_yaml()   │
    │               │ │              │  │                   │
    │kubectl get    │ │kubectl       │  │kubectl get <type>│
    │pods -n <ns>   │ │describe pod  │  │<name> -o yaml    │
    │-o wide        │ │<name> -n <ns>│  │                  │
    └───────────────┘ └──────────────┘  └──────────────────┘

    ┌───────────────┐ ┌──────────────┐  ┌──────────────────┐
    │kubectl_get_   │ │kubectl_get_  │  │kubectl_get_      │
    │events()       │ │pod_logs()    │  │deployments()     │
    └───────────────┘ └──────────────┘  └──────────────────┘

    ┌───────────────┐ ┌──────────────┐  ┌──────────────────┐
    │kubectl_top_   │ │kubectl_get_  │  │kubectl_get_      │
    │pods()         │ │nodes()       │  │namespaces()      │
    └───────────────┘ └──────────────┘  └──────────────────┘

    ┌──────────────────────────────────────┐
    │kubectl_get_deployment_history()      │
    └──────────────────────────────────────┘
```

### Plan B Key Differences from Plan A

| Aspect | Plan A | Plan B |
|--------|--------|--------|
| Tool delivery | MCP (HTTP/SSE to remote servers) | `subprocess.run(["kubectl", ...])` |
| Tool wrapping | `McpToolset(connection_params=...)` | `FunctionTool(kubectl_get_pods)` |
| Agent count | 6 (1 root + 5 sub-agents) | 1 single agent |
| Delegation | `transfer_to_agent()` auto-tool | N/A — one agent does everything |
| GCP dependency | Yes (IAM, OAuth tokens) | No (just kubectl in PATH) |
| Instruction | Dynamic (GCP vs Local sections) | Static `_SIMPLE_AGENT_INSTRUCTION` |
| Log analysis | Via Cloud Observability MCP | Not available (kubectl only) |

---

## 4. How Google ADK Works

### The Three Core ADK Objects

```python
# 1. AGENT — Configuration object (does NOT call Gemini yet)
from google.adk.agents import Agent

agent = Agent(
    name="thenightops",               # Agent identifier
    model="gemini-3.1-pro-preview",   # Which Gemini model to use
    instruction="You are an SRE...",  # System prompt sent to Gemini
    tools=[...],                       # Tools Gemini is allowed to call
    sub_agents=[...],                  # Other agents Gemini can delegate to
)

# 2. RUNNER — The engine that manages the Gemini conversation loop
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

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

# 3. RUN_ASYNC — This is where Gemini actually gets called (repeatedly!)
from google.genai import types

user_message = types.Content(
    role="user",
    parts=[types.Part(text="INCIDENT: Pod crashlooping...")],
)

async for event in runner.run_async(
    user_id="nightops-system",
    session_id=session.id,
    new_message=user_message,
):
    # Each 'event' is one response step from Gemini
    # Could be: tool call, text response, agent transfer, etc.
    pass
```

### ADK Event Types

When iterating over `runner.run_async()`, each event can be:

| Event Type | What It Means | How to Detect |
|-----------|---------------|---------------|
| **Tool call** | Gemini wants to call a function | `event.get_function_calls()` returns list |
| **Tool result** | ADK returning tool output to Gemini | `event.get_function_responses()` returns list |
| **Text response** | Gemini is producing text output | `event.content.parts[0].text` has content |
| **Agent transfer** | Gemini delegating to a sub-agent | `event.author` changes to sub-agent name |
| **Final response** | Last message in the conversation | `event.is_final_response()` returns True |

### How ADK Wraps Tools

**Plan A — MCP Tools:**
```python
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url="https://container.googleapis.com/mcp",  # Official GKE MCP
    ),
    header_provider=_create_gcp_header_provider(project_id),  # OAuth2 token
)

# ADK auto-discovers available tools from the MCP server
# and registers them as callable functions for Gemini
```

**Plan B — Function Tools:**
```python
from google.adk.tools import FunctionTool

# ADK wraps a plain Python function as a tool
tool = FunctionTool(kubectl_get_pods)

# ADK reads the function's:
#   - name (kubectl_get_pods)
#   - docstring (becomes tool description for Gemini)
#   - type hints (becomes parameter schema for Gemini)
# And registers it so Gemini can call it
```

---

## 5. Where & How Gemini Gets Called

### The Critical Insight

**You never call Gemini directly in your code.** The ADK `Runner` handles all Gemini API calls internally.

### The Connection Chain

```
YOUR CODE                           ADK (library)                    GEMINI API
─────────                           ────────────                     ──────────

agent = Agent(model="gemini-3.1-pro-preview")
  ↓
runner = Runner(agent=agent)
  ↓
runner.run_async(new_message=...)
                                    Runner internally calls:
                                    ┌──────────────────────────────┐
                                    │ POST generativelanguage.     │
                                    │   googleapis.com/v1/models/  │ ──────→  Gemini API
                                    │   gemini-3.1-pro-preview:    │
                                    │   generateContent            │
                                    │                              │
                                    │ Body:                        │
                                    │ {                            │
                                    │   system_instruction: "...", │ ← agent.instruction
                                    │   contents: [...],           │ ← conversation history
                                    │   tools: [{                  │ ← agent.tools schemas
                                    │     function_declarations:   │
                                    │       [{name, parameters}]   │
                                    │   }]                         │
                                    │ }                            │
                                    └──────────────────────────────┘
```

### What Gemini Sees (Per API Call)

Each time ADK calls Gemini, it sends:

1. **System Instruction** — The agent's `instruction` string (investigation protocol, available tools, output format)
2. **Conversation History** — All previous messages, tool calls, and tool results
3. **Tool Definitions** — JSON schemas of every registered tool (name, description, parameters)
4. **User Message** — The incident description (first call) or accumulated context

### Gemini Responds With One Of

| Response | What Happens Next |
|----------|------------------|
| `function_call: kube_get(kind="pods")` | ADK executes the tool, sends result back to Gemini → **LOOP CONTINUES** |
| `function_call: transfer_to_agent("log_analyst")` | ADK switches to sub-agent, starts new Gemini conversation → **LOOP CONTINUES** |
| `text: "**Incident Summary**: Pod OOMKilled..."` | If `is_final_response()` → **LOOP ENDS** |

### The Model Configuration

```python
# src/core/config.py line 256
class AgentConfig(BaseSettings):
    model: str = "gemini-3.1-pro-preview"   # ← THIS is what connects to Gemini

# Supported models (config.py line 241):
SUPPORTED_MODELS = [
    "gemini-3.1-pro-preview",   # Latest, most advanced (default)
    "gemini-3-pro-preview",     # Gemini 3 Pro preview
    "gemini-3-flash-preview",   # Fast Gemini 3 variant
    "gemini-2.5-pro",           # Advanced reasoning, stable
    "gemini-2.5-flash",         # Fast, proven stable
    "gemini-2.0-flash",         # Previous generation, reliable
]
```

Change this one string → every agent uses a different model.

---

## 6. Function Call Chain — Plan A

### Part 1: Three-Column Trace (Your Code → ADK → Gemini)

```
YOUR CODE                           ADK (library)                    GEMINI API
─────────                           ────────────                     ──────────

root_orchestrator.py:524
  agent = create_root_orchestrator()
root_orchestrator.py:532
  runner = Runner(agent=agent)
root_orchestrator.py:601
  async for event in                  Runner.run_async()  ──────→  POST /generateContent
    runner.run_async():                 │                           (model=gemini-3.1-pro)
                                        │                              │
    event.get_function_calls()  ←───── Gemini says "call kube_get" ←──┘
                                        │
                                        ├─→ McpToolset executes ───→ MCP Server
                                        │   (HTTP/SSE)                 │
                                        │                              │
                                        ├─→ Sends result to  ──────→ POST /generateContent
                                        │   Gemini again               │
                                        │                              │
                                        ├─→ Gemini says             ←──┘
                                        │   "transfer_to_agent"
                                        │
                                        ├─→ Switches agent ────────→ POST /generateContent
                                        │   (new instruction)        (same model, new prompt)
                                        │                              │
    event.content.parts[0].text ←───── Gemini final text ←───────────┘
    event.is_final_response()
```

### Part 2: Complete Setup Trace (CLI to Agent Creation)

```
USER runs:
  nightops agent run --incident "Pod crashlooping in default namespace"

STEP 1: CLI Entry
  src/cli.py → agent_run() command
    ↓
  Loads NightOpsConfig from config/.env + config/nightops.yaml
    ↓
  Calls: run_investigation(config, incident_description)

STEP 2: Investigation Setup (root_orchestrator.py:456-523)
  run_investigation():
    ↓
    # Intelligence layer — find similar past incidents
    IncidentMemory(config).find_similar(incident_description)  # TF-IDF matching
    ↓
    # Remediation policies
    PolicyEngine(config).get_policy_summary()
    ↓
    # Validate MCP config
    ↓
    agent = create_root_orchestrator(config)   # ← Creates all agents + MCP connections

STEP 3: Agent Creation (root_orchestrator.py:386-453)
  create_root_orchestrator(config):
    ↓
    mcp_toolsets = create_mcp_toolsets(config)   # ← Connects to MCP servers
    ↓
    # Create 5 sub-agents (each with same MCP toolsets)
    log_analyst           = create_log_analyst_agent(model, tools=mcp_toolsets)
    deployment_correlator = create_deployment_correlator_agent(model, tools=mcp_toolsets)
    runbook_retriever     = create_runbook_retriever_agent(model, tools=mcp_toolsets)
    communication_drafter = create_communication_drafter_agent(model)  # NO tools
    anomaly_detector      = create_anomaly_detector_agent(model, tools=mcp_toolsets)
    ↓
    # Create root agent
    root_agent = Agent(
        name="thenightops",
        model=model,
        instruction=_build_root_instruction(use_gcp),
        sub_agents=[log_analyst, deployment_correlator, runbook_retriever,
                    communication_drafter, anomaly_detector],
        tools=[*mcp_toolsets],   # Root also has direct tool access
    )

STEP 4: Runner Execution (root_orchestrator.py:526-604)
  runner = Runner(agent=root_agent, ...)
  session = await session_service.create_session(...)
    ↓
  user_message = types.Content(
      role="user",
      parts=[types.Part(text="INCIDENT RECEIVED:\n\n{incident}\n\n{history}\n\n{policies}")]
  )
    ↓
  async for event in runner.run_async(user_id, session_id, new_message):
      # GEMINI IS CALLED HERE — REPEATEDLY IN A LOOP
      #
      # Each 'event' is one step:
      #   - Gemini calls a tool → ADK executes it → sends result back
      #   - Gemini delegates to sub-agent → ADK switches context
      #   - Gemini produces final text → loop ends
```

### Part 3: Call-by-Call Gemini Sequence (10-20 API calls)

```
CALL 1 → Gemini (as root_orchestrator)
  Input:  instruction + incident + history + policies + tool list
  Output: "I'll call kube_get to check pods"
  → function_call: kube_get(kind="pods")

  ADK executes: MCP → https://container.googleapis.com/mcp
  Returns: pod status table to Gemini

CALL 2 → Gemini (as root_orchestrator)
  Input:  previous context + pod status result
  Output: "I see CrashLoopBackOff. Let me delegate to log_analyst"
  → function_call: transfer_to_agent("log_analyst")

  ╔═══════════════════════════════════════════════════════════╗
  ║  ADK now switches to log_analyst agent                    ║
  ║  New Gemini session with log_analyst's instruction        ║
  ║                                                           ║
  ║  CALL 3 → Gemini (as log_analyst)                        ║
  ║    Input:  log_analyst instruction + incident context     ║
  ║    Output: "I'll query error logs"                        ║
  ║    → function_call: list_log_entries(severity>=ERROR)     ║
  ║                                                           ║
  ║    ADK executes: MCP → https://logging.googleapis.com/mcp║
  ║    Returns: error log entries to Gemini                   ║
  ║                                                           ║
  ║  CALL 4 → Gemini (as log_analyst)                        ║
  ║    Input:  previous + log results                         ║
  ║    Output: "Pattern: OOM errors spiking since 14:30 UTC  ║
  ║             Evidence: 47 OOMKilled events...              ║
  ║             Confidence: HIGH"                             ║
  ║    → text response (findings)                             ║
  ╚═══════════════════════════════════════════════════════════╝

  ADK returns log_analyst findings to root

CALL 5 → Gemini (as root_orchestrator)
  Input:  previous context + log_analyst findings
  Output: "Now let me check deployments"
  → function_call: transfer_to_agent("deployment_correlator")

  ╔═══════════════════════════════════════════════════════════╗
  ║  CALL 6 → Gemini (as deployment_correlator)              ║
  ║  CALL 7 → Gemini (as deployment_correlator)              ║
  ║  ... tools + findings returned                           ║
  ╚═══════════════════════════════════════════════════════════╝

CALL 8 → Gemini (as root_orchestrator)
  ... delegates to runbook_retriever, anomaly_detector ...

CALL N-1 → Gemini (as root_orchestrator)
  Input:  ALL findings from all sub-agents
  Output: "Based on all evidence, here's my synthesis..."
  → function_call: transfer_to_agent("communication_drafter")

  ╔═══════════════════════════════════════════════════════════╗
  ║  CALL N → Gemini (as communication_drafter)              ║
  ║    Input:  all findings passed in context                 ║
  ║    Output: Formatted RCA text (NO tools — just writing)  ║
  ║    "**Incident Summary**: Pod demo-app OOMKilled..."     ║
  ╚═══════════════════════════════════════════════════════════╝

FINAL CALL → Gemini (as root_orchestrator)
  Input:  everything + communication_drafter's RCA
  Output: Final formatted response
  → is_final_response() == True → DONE
```

### MCP Connection Details

```python
# Official GKE MCP (root_orchestrator.py:250-270)
McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url="https://container.googleapis.com/mcp",
    ),
    header_provider=_create_gcp_header_provider(
        project_id,
        extra_headers={
            "x-gke-cluster": cluster_name,
            "x-gke-location": location,
        },
    ),
)

# Official Cloud Observability MCP (root_orchestrator.py:233-248)
McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url="https://logging.googleapis.com/mcp",
    ),
    header_provider=_create_gcp_header_provider(project_id),
)

# OAuth2 Token Provider (root_orchestrator.py:41-65)
def _create_gcp_header_provider(project_id):
    def provider(context):
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(google.auth.transport.requests.Request())
        return {
            "Authorization": f"Bearer {credentials.token}",
            "x-goog-project-id": project_id,
        }
    return provider
```

---

## 7. Function Call Chain — Plan B

### Part 1: Three-Column Trace (Your Code → ADK → Gemini)

```
YOUR CODE                           ADK (library)                    GEMINI API
─────────                           ────────────                     ──────────

simple_agent.py:347
  agent = create_simple_agent()
simple_agent.py:355
  runner = Runner(agent=agent)
simple_agent.py:407
  async for event in                  Runner.run_async()  ──────→  POST /generateContent
    runner.run_async():                 │                           (model=gemini-3.1-pro)
                                        │                              │
    event.get_function_calls()  ←───── Gemini says "kubectl_get_pods"←┘
                                        │
                                        ├─→ FunctionTool executes
                                        │   subprocess.run(["kubectl",...])
                                        │
                                        ├─→ Sends kubectl output ──→ POST /generateContent
                                        │   back to Gemini             │
                                        │                              │
    event.content.parts[0].text ←───── Gemini final text ←───────────┘
    event.is_final_response()
```

### Part 2: Complete Setup Trace (CLI to Agent Creation)

```
USER runs:
  nightops agent run --simple --incident "Pod crashlooping in default namespace"

STEP 1: CLI Entry
  src/cli.py → agent_run() command (detects --simple flag)
    ↓
  Loads NightOpsConfig
    ↓
  Calls: run_simple_investigation(config, incident_description)

STEP 2: Investigation Setup (simple_agent.py:332-346)
  run_simple_investigation():
    ↓
    agent = create_simple_agent(config)

STEP 3: Agent Creation (simple_agent.py:292-329)
  create_simple_agent(config):
    ↓
    tools = [
        FunctionTool(kubectl_get_pods),              # subprocess → kubectl get pods
        FunctionTool(kubectl_get_events),             # subprocess → kubectl get events
        FunctionTool(kubectl_describe_pod),            # subprocess → kubectl describe pod
        FunctionTool(kubectl_get_pod_logs),            # subprocess → kubectl logs
        FunctionTool(kubectl_get_deployments),         # subprocess → kubectl get deployments
        FunctionTool(kubectl_get_deployment_history),  # subprocess → kubectl rollout history
        FunctionTool(kubectl_top_pods),                # subprocess → kubectl top pods
        FunctionTool(kubectl_get_nodes),               # subprocess → kubectl get nodes
        FunctionTool(kubectl_get_resource_yaml),       # subprocess → kubectl get -o yaml
        FunctionTool(kubectl_get_namespaces),          # subprocess → kubectl get namespaces
    ]
    ↓
    agent = Agent(
        name="thenightops_simple",
        model=config.agent.model,
        instruction=_SIMPLE_AGENT_INSTRUCTION,
        tools=tools,
        # NO sub_agents — single agent does everything
    )

STEP 4: Runner Execution (simple_agent.py:349-410)
  runner = Runner(agent=agent, ...)
  session = await session_service.create_session(...)
    ↓
  user_message = types.Content(
      role="user",
      parts=[types.Part(text="INCIDENT RECEIVED:\n\n{incident}")]
  )
    ↓
  async for event in runner.run_async(user_id, session_id, new_message):
      # GEMINI IS CALLED HERE — REPEATEDLY IN A LOOP
```

### Part 3: Call-by-Call Gemini Sequence (5-10 API calls)

```
CALL 1 → Gemini (as thenightops_simple)
  Input:  instruction + "INCIDENT RECEIVED:\n\nPod crashlooping..."
  Output: "Let me start by checking namespaces and pods"
  → function_call: kubectl_get_namespaces()

  ADK executes: subprocess.run(["kubectl", "get", "namespaces"])
  Returns: namespace list to Gemini

CALL 2 → Gemini (as thenightops_simple)
  Input:  previous + namespace list
  Output: "Checking pods in default namespace"
  → function_call: kubectl_get_pods("default")

  ADK executes: subprocess.run(["kubectl", "get", "pods", "-n", "default", "-o", "wide", "--show-labels"])
  Returns: pod table showing CrashLoopBackOff

CALL 3 → Gemini (as thenightops_simple)
  Input:  previous + pod status
  Output: "demo-app is CrashLoopBackOff with 15 restarts. Let me check events"
  → function_call: kubectl_get_events("default")

  ADK executes: subprocess.run(["kubectl", "get", "events", "-n", "default", "--sort-by", ".lastTimestamp"])
  Returns: events showing OOMKilled

CALL 4 → Gemini (as thenightops_simple)
  Input:  previous + events
  Output: "OOMKilled detected. Getting pod details"
  → function_call: kubectl_describe_pod("demo-app")

  ADK executes: subprocess.run(["kubectl", "describe", "pod", "demo-app", "-n", "default"])
  Returns: full pod description with memory limit 128Mi

CALL 5 → Gemini (as thenightops_simple)
  Input:  previous + pod description
  Output: "Checking previous container logs"
  → function_call: kubectl_get_pod_logs("demo-app", previous=True)

  ADK executes: subprocess.run(["kubectl", "logs", "demo-app", "-n", "default", "--tail", "100", "--previous"])
  Returns: log showing memory allocation before crash

CALL 6 → Gemini (as thenightops_simple)
  Input:  previous + logs
  Output: "Checking resource limits in YAML"
  → function_call: kubectl_get_resource_yaml("pod", "demo-app")

  ADK executes: subprocess.run(["kubectl", "get", "pod", "demo-app", "-n", "default", "-o", "yaml"])
  Returns: YAML showing limits.memory: 128Mi

CALL 7 → Gemini (as thenightops_simple)          ← FINAL
  Input:  ALL previous context + all tool results
  Output:
    "**Incident Summary**: Pod demo-app OOMKilled repeatedly
     **Severity**: critical
     **Root Cause**: Memory limit 128Mi is insufficient for workload
     **Evidence**: 15 restarts, OOMKilled events, memory limit in YAML
     **Immediate Actions**: Increase memory limit to 512Mi
     **Long-term**: Add memory monitoring alerts
     **Confidence**: HIGH"
  → is_final_response() == True → DONE
```

### How kubectl Tools Work Internally

```python
# Every tool follows the same pattern (simple_agent.py:183-203):

def _run_kubectl(args: list[str], timeout: int = 30) -> str:
    """Execute a kubectl command and return its output."""
    cmd = ["kubectl"] + args
    result = subprocess.run(
        cmd,
        capture_output=True,   # Capture stdout + stderr
        text=True,             # Return as string
        timeout=timeout,       # 30 second timeout
    )
    if result.returncode != 0:
        return f"Error: {result.stderr.strip()}"
    return result.stdout.strip()

# Example: kubectl_get_pods("default") runs:
#   subprocess.run(["kubectl", "get", "pods", "-n", "default", "-o", "wide", "--show-labels"])
```

---

## 8. The ADK Runner Loop (Internal)

This is what happens inside `runner.run_async()` — you don't write this code, but understanding it is essential:

```
runner.run_async() is a LOOP — it calls Gemini MULTIPLE times:

┌─────────────────────────────────────────────────────────────────┐
│  ADK Runner Internal Loop (you don't write this code)           │
│                                                                  │
│  STEP 1: Send to Gemini API                                     │
│  ┌──────────────────────────────────────────────────────┐       │
│  │ POST https://generativelanguage.googleapis.com/v1/   │       │
│  │      models/gemini-3.1-pro-preview:generateContent   │       │
│  │                                                       │       │
│  │ Body:                                                 │       │
│  │ {                                                     │       │
│  │   "system_instruction": "You are TheNightOps...",     │       │
│  │   "contents": [                                       │       │
│  │     {"role": "user", "parts": [                       │       │
│  │       {"text": "INCIDENT RECEIVED:\n\nPod crash..."}  │       │
│  │     ]}                                                │       │
│  │   ],                                                  │       │
│  │   "tools": [                                          │       │
│  │     {"function_declarations": [                       │       │
│  │       {"name": "kube_get", "parameters": {...}},      │       │
│  │       {"name": "list_log_entries", ...},               │       │
│  │       {"name": "transfer_to_agent", ...}              │       │
│  │     ]}                                                │       │
│  │   ]                                                   │       │
│  │ }                                                     │       │
│  └──────────────────────────────────────────────────────┘       │
│         │                                                        │
│         ▼                                                        │
│  STEP 2: Gemini responds with either:                            │
│                                                                  │
│  Option A: "Call a tool"                                         │
│  {"function_call": {"name": "kube_get",                         │
│                      "args": {"kind": "pods", "ns": "default"}}} │
│         │                                                        │
│         ▼                                                        │
│  STEP 3: ADK executes the tool (MCP call or kubectl subprocess)  │
│         │                                                        │
│         ▼                                                        │
│  STEP 4: ADK sends tool result BACK to Gemini                   │
│  {"function_response": {"name": "kube_get",                     │
│   "response": "NAME       READY  STATUS           RESTARTS\n    │
│                demo-app   0/1    CrashLoopBackOff  15"}}         │
│         │                                                        │
│         ▼                                                        │
│  → BACK TO STEP 1 (Gemini sees the tool result, decides next)   │
│                                                                  │
│  Option B: "Transfer to sub-agent"  (Plan A only)                │
│  {"function_call": {"name": "transfer_to_agent",                │
│                      "args": {"agent": "log_analyst"}}}          │
│         │                                                        │
│         ▼                                                        │
│  ADK switches to log_analyst agent (different model instruction) │
│  → Starts a NEW Gemini conversation with log_analyst's prompt    │
│  → log_analyst calls tools, gets results, produces findings      │
│  → ADK returns findings back to root agent                       │
│  → BACK TO STEP 1 (root sees log_analyst's findings)             │
│                                                                  │
│  Option C: "Final text response"                                 │
│  {"text": "**Incident Summary**: Pod demo-app OOMKilled...\n    │
│            **Root Cause**: Memory limit 128Mi insufficient..."}   │
│         │                                                        │
│         ▼                                                        │
│  STEP 5: event.is_final_response() == True → LOOP ENDS          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 9. Agent Files — Quick Reference

| File | Agent Name | Role | Has Tools? | Has Sub-Agents? |
|------|-----------|------|-----------|----------------|
| `src/agents/root_orchestrator.py` | `thenightops` | Coordinates investigation | Yes (MCP) | Yes (5) |
| `src/agents/simple_agent.py` | `thenightops_simple` | Single-agent investigator | Yes (kubectl) | No |
| `src/agents/log_analyst.py` | `log_analyst` | Cloud log analysis | Yes (MCP) | No |
| `src/agents/deployment_correlator.py` | `deployment_correlator` | K8s deployment/pod checks | Yes (MCP) | No |
| `src/agents/runbook_retriever.py` | `runbook_retriever` | Historical patterns | Yes (MCP) | No |
| `src/agents/communication_drafter.py` | `communication_drafter` | RCA & comms writing | **No** (text only) | No |
| `src/agents/anomaly_detector.py` | `anomaly_detector` | Proactive health checks | Yes (MCP) | No |

---

## 10. Sub-Agent Details

### Log Analyst (`src/agents/log_analyst.py`)

**Purpose**: Analyzes Cloud Logging data for error patterns, anomalies, and trace correlations.

**GCP Mode Tools**:
- `list_log_entries` — Query Cloud Logging with filter expressions
- `list_log_names` — List available log names

**Local Mode Tools**:
- `query_logs` — Query Cloud Logging
- `detect_error_patterns` — Find recurring error patterns
- `get_log_volume_anomalies` — Find log volume spikes
- `correlate_logs_by_trace` — Trace correlation

**Investigation Protocol**: Initial triage → Error pattern detection → Deep dive → Correlation

**Output Format**: Pattern, Evidence, Significance, Confidence

---

### Deployment Correlator (`src/agents/deployment_correlator.py`)

**Purpose**: Checks recent K8s deployments, config changes, and pod health to correlate with incident timing.

**GCP Mode Tools**:
- `kube_get` — Get any K8s resource (deployments, pods, events, replicasets)
- `kube_api_resources` — List available API resource types
- `list_node_pools`, `get_node_pool`

**Local Mode Tools**:
- `get_deployments`, `get_pod_status`, `get_pod_logs`, `get_events`, `get_resource_usage`, `describe_pod`

**Investigation Protocol**: Recent deployments → Pod health → K8s events → Resource analysis → Deep dive

**Output Format**: Change Detected, Correlation, Impact Assessment, Confidence

---

### Runbook Retriever (`src/agents/runbook_retriever.py`)

**Purpose**: Finds historical context, past incident patterns, and known resolutions.

**Unique Feature**: Works with the **Incident Memory** system (TF-IDF similarity matching from `src/intelligence/incident_memory.py`). The Root Orchestrator enriches the message with historical context before sending.

**GCP Mode Tools**: `kube_get`, `list_log_entries`
**Local Mode Tools**: `get_events`, `query_logs`, `detect_error_patterns`, `get_deployments`

**Investigation Protocol**: Historical pattern matching → Event context → Log history → Deployment timeline

**Output Format**: Similar Past Incidents, Historical Patterns, Event Timeline, Suggested Remediation, Confidence

---

### Anomaly Detector (`src/agents/anomaly_detector.py`)

**Purpose**: **PROACTIVE** — doesn't wait for incidents. Actively looks for problems before they cause outages.

**Health Checks**:
1. Pod Health (CrashLoopBackOff, restarts, pending pods)
2. Memory Trending (pods near limits, usage increases)
3. Error Rate (above baseline, new error types, increasing rates)
4. Resource Exhaustion (node pressure, PVC capacity)
5. Deployment Health (rollout failures, image pull errors)

**Output Format**: Check, Status (HEALTHY/WARNING/CRITICAL), Details, Action

---

### Communication Drafter (`src/agents/communication_drafter.py`)

**Purpose**: Generates incident communications — RCA reports and stakeholder updates.

**Tools**: **NONE** — This is the only agent with no tools. It only produces text.

**Communication Types**:
1. Incident Status Updates (for #incidents)
2. RCA Summaries (for #post-mortems)
3. Stakeholder Notifications (non-technical, for business channels)
4. Multi-Channel Alerts (Email, Telegram, WhatsApp — when enabled)

---

## 11. MCP Servers & Tool Mapping

### Official GCP MCP Servers (Plan A default)

| MCP Server | Endpoint | Auth | Tools |
|-----------|----------|------|-------|
| **GKE MCP** | `https://container.googleapis.com/mcp` | OAuth2 Bearer + `x-goog-project-id` + `x-gke-cluster` + `x-gke-location` | `kube_get`, `kube_api_resources`, `list_clusters`, `get_cluster`, `list_node_pools`, `get_node_pool` |
| **Cloud Observability MCP** | `https://logging.googleapis.com/mcp` | OAuth2 Bearer + `x-goog-project-id` | `list_log_entries`, `list_log_names` |

### Custom MCP Servers (Plan A --local mode)

| MCP Server | Source File | Port | Transport | Tools |
|-----------|------------|------|-----------|-------|
| **Kubernetes** | `src/mcp_servers/kubernetes/server.py` | 8001 | SSE | `get_pod_status`, `get_pod_logs`, `get_events`, `get_deployments`, `get_resource_usage`, `describe_pod` |
| **Cloud Logging** | `src/mcp_servers/cloud_logging/server.py` | 8002 | SSE | `query_logs`, `detect_error_patterns`, `get_log_volume_anomalies`, `correlate_logs_by_trace` |
| **Slack** | `src/mcp_servers/slack/server.py` | 8003 | SSE | (disabled by default) |
| **Notifications** | `src/mcp_servers/notifications/server.py` | 8004 | SSE | Email, Telegram, WhatsApp (disabled by default) |

### Optional: Grafana MCP

| MCP Server | Transport | Config |
|-----------|-----------|--------|
| **Grafana** | stdio (via `uvx mcp-grafana`) | `GRAFANA_URL` + `GRAFANA_SERVICE_ACCOUNT_TOKEN` |

### Plan B — No MCP (kubectl subprocess)

| Python Function | kubectl Command | Purpose |
|----------------|----------------|---------|
| `kubectl_get_pods(ns)` | `kubectl get pods -n {ns} -o wide --show-labels` | Pod status |
| `kubectl_get_events(ns)` | `kubectl get events -n {ns} --sort-by .lastTimestamp` | K8s events |
| `kubectl_describe_pod(pod, ns)` | `kubectl describe pod {pod} -n {ns}` | Pod details |
| `kubectl_get_pod_logs(pod, ns, tail, prev)` | `kubectl logs {pod} -n {ns} --tail {tail} [--previous]` | Container logs |
| `kubectl_get_deployments(ns)` | `kubectl get deployments -n {ns} -o wide` | Deployment status |
| `kubectl_get_deployment_history(dep, ns)` | `kubectl rollout history deployment/{dep} -n {ns}` | Rollout history |
| `kubectl_top_pods(ns)` | `kubectl top pods -n {ns}` | CPU/memory usage |
| `kubectl_get_nodes()` | `kubectl get nodes -o wide` | Node status |
| `kubectl_get_resource_yaml(type, name, ns)` | `kubectl get {type} {name} -n {ns} -o yaml` | Full YAML |
| `kubectl_get_namespaces()` | `kubectl get namespaces` | Namespace list |

---

## 12. Key Patterns to Remember

### 1. You Never Call Gemini Directly
ADK's `Runner.run_async()` handles all Gemini API calls. You provide the `Agent` (model + instruction + tools) and the `Runner` does the rest.

### 2. Gemini Is the Brain That Decides
Your code provides tools and instructions. **Gemini decides** which tools to call, in what order, and how to interpret results. The investigation protocol in the instruction guides Gemini's decision-making.

### 3. Each Sub-Agent Gets Its Own Gemini Conversation
When the root orchestrator delegates to `log_analyst`, ADK starts a completely new Gemini conversation with `log_analyst`'s instruction. The sub-agent has its own specialized prompt and context.

### 4. MCP vs kubectl = Same Concept, Different Transport
Both plans give Gemini tools to query Kubernetes. Plan A uses HTTP/SSE to MCP servers. Plan B uses subprocess calls to kubectl. The Gemini experience is the same — "here are tools, investigate this incident."

### 5. The Instruction IS the Investigation Protocol
The `_ROOT_ORCHESTRATOR_BASE` and `_SIMPLE_AGENT_INSTRUCTION` strings are the most important pieces of the system. They define the 4-phase investigation protocol that Gemini follows.

### 6. FunctionTool Auto-Discovers from Python
For Plan B, ADK reads the function name, docstring, and type hints to create the tool schema. Good docstrings = better tool usage by Gemini.

### 7. Dashboard Integration Is Event-Driven
Both plans push events to the dashboard via HTTP (`_push_dashboard_event()` / `_push_event()`). This is independent of the agent loop — just event reporting.

---

## 13. File Navigation Map

### "I want to understand..." → Go to this file

| Question | File | Key Function/Section |
|----------|------|---------------------|
| How does the CLI work? | `src/cli.py` | `agent_run()`, `agent_watch()` |
| How is the root agent created? | `src/agents/root_orchestrator.py` | `create_root_orchestrator()` (line 386) |
| How does Plan A investigation run? | `src/agents/root_orchestrator.py` | `run_investigation()` (line 456) |
| How is the simple agent created? | `src/agents/simple_agent.py` | `create_simple_agent()` (line 292) |
| How does Plan B investigation run? | `src/agents/simple_agent.py` | `run_simple_investigation()` (line 332) |
| What MCP servers are connected? | `src/agents/root_orchestrator.py` | `create_mcp_toolsets()` (line 224) |
| How does OAuth2 auth work? | `src/agents/root_orchestrator.py` | `_create_gcp_header_provider()` (line 41) |
| What is the investigation prompt? | `src/agents/root_orchestrator.py` | `_ROOT_ORCHESTRATOR_BASE` (line 67) |
| What is the simple agent prompt? | `src/agents/simple_agent.py` | `_SIMPLE_AGENT_INSTRUCTION` (line 208) |
| How do kubectl tools work? | `src/agents/simple_agent.py` | `_run_kubectl()` (line 183), tool functions (lines 40-180) |
| How does log analysis work? | `src/agents/log_analyst.py` | `_LOG_ANALYST_GCP_INSTRUCTION` / `_LOCAL` |
| How does deployment correlation work? | `src/agents/deployment_correlator.py` | `_DEPLOYMENT_CORRELATOR_GCP_INSTRUCTION` / `_LOCAL` |
| How does anomaly detection work? | `src/agents/anomaly_detector.py` | `_ANOMALY_DETECTOR_GCP_INSTRUCTION` / `_LOCAL` |
| How does RCA generation work? | `src/agents/communication_drafter.py` | `COMMUNICATION_DRAFTER_INSTRUCTION` |
| How does historical matching work? | `src/intelligence/incident_memory.py` | `IncidentMemory.find_similar()` |
| How does config load? | `src/core/config.py` | `NightOpsConfig`, `AgentConfig` |
| What Gemini model is used? | `src/core/config.py` | `AgentConfig.model` (line 256) |
| How does the webhook receiver work? | `src/ingestion/webhook_receiver.py` | FastAPI app, `/api/v1/webhooks/*` |
| How does deduplication work? | `src/ingestion/deduplication.py` | Fingerprint-based sliding window |
| How does remediation policy work? | `src/remediation/policy_engine.py` | `PolicyEngine`, 4 levels |
| How does the dashboard work? | `src/dashboard/app.py` | FastAPI + WebSocket on :8888 |
| How do metrics work? | `src/metrics/tracker.py` | MTTR, impact summaries |
| How does proactive scanning work? | `src/proactive/scheduler.py` | Periodic anomaly detection |

---

## Quick Command Reference

```bash
# Plan A — Full MCP (requires GCP setup)
nightops agent run --incident "Pod crashlooping in default namespace"

# Plan B — Simple kubectl (just needs kubectl)
nightops agent run --simple --incident "Pod crashlooping in default namespace"

# Plan A with local MCP servers (no GCP)
nightops agent run --local --incident "Pod crashlooping in default namespace"

# Autonomous mode (webhook-triggered)
nightops agent watch           # Plan A
nightops agent watch --simple  # Plan B

# Interactive mode
nightops agent run --interactive         # Plan A
nightops agent run --simple --interactive  # Plan B
```

---

*Developed with ❤️ by [Mehul Patel](https://nomadicmehul.dev/) — for the love of DevOps, Cloud Native, and the Open Source community.*
