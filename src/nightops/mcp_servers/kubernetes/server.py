"""
Kubernetes MCP Server for TheNightOps.

Provides tools to inspect pod status, read events, check resource utilisation,
review recent deployments, and retrieve container logs from a Kubernetes cluster.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.routing import Mount, Route

logger = logging.getLogger(__name__)

server = Server("nightops-kubernetes")

# ── Kubernetes Client Setup ─────────────────────────────────────────


def _load_kube_config(context: str = "", in_cluster: bool = False) -> None:
    """Load Kubernetes configuration. Auto-detects in-cluster environment."""
    if in_cluster:
        config.load_incluster_config()
    else:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config(context=context if context else None)


def _get_core_api() -> client.CoreV1Api:
    return client.CoreV1Api()


def _get_apps_api() -> client.AppsV1Api:
    return client.AppsV1Api()


# ── Tool Definitions ────────────────────────────────────────────────


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_pod_status",
            description=(
                "Get the status of pods in a namespace, including container states, "
                "restart counts, and resource usage. Optionally filter by label selector."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "namespace": {
                        "type": "string",
                        "description": "Kubernetes namespace (default: default)",
                        "default": "default",
                    },
                    "label_selector": {
                        "type": "string",
                        "description": "Label selector to filter pods, e.g. 'app=myservice'",
                        "default": "",
                    },
                    "context": {"type": "string", "default": ""},
                    "in_cluster": {"type": "boolean", "default": False},
                },
                "required": [],
            },
        ),
        Tool(
            name="get_pod_logs",
            description=(
                "Retrieve container logs from a specific pod. Supports tail lines "
                "and container selection for multi-container pods."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pod_name": {"type": "string", "description": "Name of the pod"},
                    "namespace": {"type": "string", "default": "default"},
                    "container": {
                        "type": "string",
                        "description": "Container name (for multi-container pods)",
                        "default": "",
                    },
                    "tail_lines": {
                        "type": "integer",
                        "description": "Number of lines from the end (default: 100)",
                        "default": 100,
                    },
                    "previous": {
                        "type": "boolean",
                        "description": "Get logs from previous terminated container",
                        "default": False,
                    },
                    "context": {"type": "string", "default": ""},
                    "in_cluster": {"type": "boolean", "default": False},
                },
                "required": ["pod_name"],
            },
        ),
        Tool(
            name="get_events",
            description=(
                "List Kubernetes events in a namespace, optionally filtered by "
                "involved object. Events reveal OOMKills, scheduling failures, "
                "liveness probe failures, and other issues."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "default": "default"},
                    "field_selector": {
                        "type": "string",
                        "description": "Field selector, e.g. 'involvedObject.name=mypod'",
                        "default": "",
                    },
                    "event_type": {
                        "type": "string",
                        "description": "Filter by type: Normal or Warning",
                        "default": "",
                    },
                    "context": {"type": "string", "default": ""},
                    "in_cluster": {"type": "boolean", "default": False},
                },
                "required": [],
            },
        ),
        Tool(
            name="get_deployments",
            description=(
                "List deployments and their status in a namespace. Shows replica "
                "counts, images, update status, and conditions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "default": "default"},
                    "label_selector": {"type": "string", "default": ""},
                    "context": {"type": "string", "default": ""},
                    "in_cluster": {"type": "boolean", "default": False},
                },
                "required": [],
            },
        ),
        Tool(
            name="get_resource_usage",
            description=(
                "Get resource (CPU/memory) requests, limits, and current usage "
                "for pods in a namespace. Helps identify resource exhaustion."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "default": "default"},
                    "label_selector": {"type": "string", "default": ""},
                    "context": {"type": "string", "default": ""},
                    "in_cluster": {"type": "boolean", "default": False},
                },
                "required": [],
            },
        ),
        Tool(
            name="describe_pod",
            description=(
                "Get detailed information about a specific pod, similar to "
                "'kubectl describe pod'. Includes full status, conditions, "
                "container details, and recent events."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pod_name": {"type": "string"},
                    "namespace": {"type": "string", "default": "default"},
                    "context": {"type": "string", "default": ""},
                    "in_cluster": {"type": "boolean", "default": False},
                },
                "required": ["pod_name"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        context = arguments.pop("context", "")
        in_cluster = arguments.pop("in_cluster", False)
        _load_kube_config(context=context, in_cluster=in_cluster)

        if name == "get_pod_status":
            return await _get_pod_status(**arguments)
        elif name == "get_pod_logs":
            return await _get_pod_logs(**arguments)
        elif name == "get_events":
            return await _get_events(**arguments)
        elif name == "get_deployments":
            return await _get_deployments(**arguments)
        elif name == "get_resource_usage":
            return await _get_resource_usage(**arguments)
        elif name == "describe_pod":
            return await _describe_pod(**arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except ApiException as e:
        return [TextContent(type="text", text=f"Kubernetes API error: {e.status} - {e.reason}")]
    except Exception as e:
        logger.exception(f"Error executing tool {name}")
        return [TextContent(type="text", text=f"Error: {e!s}")]


# ── Tool Implementations ───────────────────────────────────────────


async def _get_pod_status(
    namespace: str = "default",
    label_selector: str = "",
) -> list[TextContent]:
    v1 = _get_core_api()
    pods = v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)

    pod_list = []
    for pod in pods.items:
        containers = []
        for cs in pod.status.container_statuses or []:
            state = "unknown"
            if cs.state.running:
                state = "running"
            elif cs.state.waiting:
                state = f"waiting: {cs.state.waiting.reason or 'unknown'}"
            elif cs.state.terminated:
                state = f"terminated: {cs.state.terminated.reason or 'unknown'}"

            containers.append(
                {
                    "name": cs.name,
                    "state": state,
                    "ready": cs.ready,
                    "restart_count": cs.restart_count,
                    "image": cs.image,
                    "last_termination": (
                        {
                            "reason": cs.last_state.terminated.reason,
                            "exit_code": cs.last_state.terminated.exit_code,
                            "finished_at": cs.last_state.terminated.finished_at.isoformat()
                            if cs.last_state.terminated.finished_at
                            else None,
                        }
                        if cs.last_state and cs.last_state.terminated
                        else None
                    ),
                }
            )

        pod_list.append(
            {
                "name": pod.metadata.name,
                "namespace": pod.metadata.namespace,
                "status": pod.status.phase,
                "node": pod.spec.node_name or "",
                "containers": containers,
                "pod_ip": pod.status.pod_ip or "",
                "start_time": (
                    pod.status.start_time.isoformat() if pod.status.start_time else None
                ),
                "labels": dict(pod.metadata.labels or {}),
            }
        )

    result = {"namespace": namespace, "total_pods": len(pod_list), "pods": pod_list}
    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def _get_pod_logs(
    pod_name: str,
    namespace: str = "default",
    container: str = "",
    tail_lines: int = 100,
    previous: bool = False,
) -> list[TextContent]:
    v1 = _get_core_api()

    kwargs: dict[str, Any] = {
        "name": pod_name,
        "namespace": namespace,
        "tail_lines": tail_lines,
        "previous": previous,
    }
    if container:
        kwargs["container"] = container

    logs = v1.read_namespaced_pod_log(**kwargs)

    result = {
        "pod": pod_name,
        "namespace": namespace,
        "container": container or "default",
        "previous": previous,
        "lines": logs.split("\n") if logs else [],
        "total_lines": len(logs.split("\n")) if logs else 0,
    }
    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def _get_events(
    namespace: str = "default",
    field_selector: str = "",
    event_type: str = "",
) -> list[TextContent]:
    v1 = _get_core_api()
    events = v1.list_namespaced_event(namespace=namespace, field_selector=field_selector)

    event_list = []
    for event in events.items:
        if event_type and event.type != event_type:
            continue
        event_list.append(
            {
                "type": event.type,
                "reason": event.reason,
                "message": event.message,
                "count": event.count or 1,
                "first_timestamp": (
                    event.first_timestamp.isoformat() if event.first_timestamp else None
                ),
                "last_timestamp": (
                    event.last_timestamp.isoformat() if event.last_timestamp else None
                ),
                "involved_object": {
                    "kind": event.involved_object.kind,
                    "name": event.involved_object.name,
                    "namespace": event.involved_object.namespace,
                },
                "source": event.source.component if event.source else "",
            }
        )

    # Sort by last timestamp, most recent first
    event_list.sort(key=lambda e: e["last_timestamp"] or "", reverse=True)

    result = {"namespace": namespace, "total_events": len(event_list), "events": event_list[:50]}
    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def _get_deployments(
    namespace: str = "default",
    label_selector: str = "",
) -> list[TextContent]:
    apps = _get_apps_api()
    deps = apps.list_namespaced_deployment(namespace=namespace, label_selector=label_selector)

    dep_list = []
    for dep in deps.items:
        images = []
        for container in dep.spec.template.spec.containers:
            images.append(container.image)

        conditions = []
        for cond in dep.status.conditions or []:
            conditions.append(
                {
                    "type": cond.type,
                    "status": cond.status,
                    "reason": cond.reason or "",
                    "message": cond.message or "",
                    "last_update": (
                        cond.last_update_time.isoformat() if cond.last_update_time else None
                    ),
                }
            )

        dep_list.append(
            {
                "name": dep.metadata.name,
                "namespace": dep.metadata.namespace,
                "replicas": dep.spec.replicas or 0,
                "ready_replicas": dep.status.ready_replicas or 0,
                "updated_replicas": dep.status.updated_replicas or 0,
                "available_replicas": dep.status.available_replicas or 0,
                "images": images,
                "conditions": conditions,
                "labels": dict(dep.metadata.labels or {}),
                "created": (
                    dep.metadata.creation_timestamp.isoformat()
                    if dep.metadata.creation_timestamp
                    else None
                ),
            }
        )

    result = {"namespace": namespace, "total_deployments": len(dep_list), "deployments": dep_list}
    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def _get_resource_usage(
    namespace: str = "default",
    label_selector: str = "",
) -> list[TextContent]:
    v1 = _get_core_api()
    pods = v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)

    usage_list = []
    for pod in pods.items:
        containers = []
        for container in pod.spec.containers:
            req = container.resources.requests or {} if container.resources else {}
            lim = container.resources.limits or {} if container.resources else {}
            containers.append(
                {
                    "name": container.name,
                    "requests": {"cpu": req.get("cpu", ""), "memory": req.get("memory", "")},
                    "limits": {"cpu": lim.get("cpu", ""), "memory": lim.get("memory", "")},
                }
            )

        # Check for OOMKilled
        oom_killed = False
        for cs in pod.status.container_statuses or []:
            if cs.last_state and cs.last_state.terminated:
                if cs.last_state.terminated.reason == "OOMKilled":
                    oom_killed = True

        usage_list.append(
            {
                "pod": pod.metadata.name,
                "status": pod.status.phase,
                "restart_count": sum(
                    cs.restart_count for cs in (pod.status.container_statuses or [])
                ),
                "oom_killed": oom_killed,
                "containers": containers,
                "node": pod.spec.node_name or "",
            }
        )

    result = {"namespace": namespace, "total_pods": len(usage_list), "pods": usage_list}
    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def _describe_pod(
    pod_name: str,
    namespace: str = "default",
) -> list[TextContent]:
    v1 = _get_core_api()
    pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)

    # Get events for this pod
    events = v1.list_namespaced_event(
        namespace=namespace,
        field_selector=f"involvedObject.name={pod_name}",
    )

    event_list = [
        {
            "type": e.type,
            "reason": e.reason,
            "message": e.message,
            "count": e.count or 1,
            "last_timestamp": e.last_timestamp.isoformat() if e.last_timestamp else None,
        }
        for e in events.items
    ]

    conditions = [
        {
            "type": c.type,
            "status": c.status,
            "reason": c.reason or "",
            "message": c.message or "",
        }
        for c in (pod.status.conditions or [])
    ]

    containers = []
    for cs in pod.status.container_statuses or []:
        state_info = {}
        if cs.state.running:
            state_info = {"state": "running", "started_at": cs.state.running.started_at.isoformat() if cs.state.running.started_at else None}
        elif cs.state.waiting:
            state_info = {"state": "waiting", "reason": cs.state.waiting.reason}
        elif cs.state.terminated:
            state_info = {
                "state": "terminated",
                "reason": cs.state.terminated.reason,
                "exit_code": cs.state.terminated.exit_code,
            }
        containers.append({**state_info, "name": cs.name, "ready": cs.ready, "restart_count": cs.restart_count, "image": cs.image})

    result = {
        "name": pod.metadata.name,
        "namespace": pod.metadata.namespace,
        "node": pod.spec.node_name or "",
        "status": pod.status.phase,
        "start_time": pod.status.start_time.isoformat() if pod.status.start_time else None,
        "labels": dict(pod.metadata.labels or {}),
        "annotations": dict(pod.metadata.annotations or {}),
        "conditions": conditions,
        "containers": containers,
        "events": event_list,
    }
    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


# ── SSE Transport ──────────────────────────────────────────────────


def create_app(server_instance: Server | None = None) -> Starlette:
    srv = server_instance or server
    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            await srv.run(streams[0], streams[1], srv.create_initialization_options())

    return Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )


if __name__ == "__main__":
    import uvicorn

    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8002)
