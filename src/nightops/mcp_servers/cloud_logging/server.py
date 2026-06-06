"""
Cloud Logging MCP Server for TheNightOps.

Provides tools to query Google Cloud Logging, detect anomaly patterns,
and correlate log entries across services. Runs as a standalone MCP server
accessible via SSE transport.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from google.cloud import logging as cloud_logging
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.routing import Mount, Route

logger = logging.getLogger(__name__)

# ── MCP Server Definition ──────────────────────────────────────────

server = Server("nightops-cloud-logging")


def _build_client(project_id: str = "") -> cloud_logging.Client:
    """Create an authenticated Cloud Logging client. Defaults to GCP_PROJECT_ID env var."""
    import os
    if not project_id:
        project_id = os.environ.get("GCP_PROJECT_ID", "")
    return cloud_logging.Client(project=project_id)


def _format_entries(entries: list[Any]) -> list[dict[str, Any]]:
    """Convert Cloud Logging entries to serialisable dicts."""
    results = []
    for entry in entries:
        results.append(
            {
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else "",
                "severity": entry.severity or "DEFAULT",
                "message": entry.payload if isinstance(entry.payload, str) else "",
                "json_payload": entry.payload if isinstance(entry.payload, dict) else {},
                "resource_type": entry.resource.type if entry.resource else "",
                "resource_labels": dict(entry.resource.labels) if entry.resource else {},
                "labels": dict(entry.labels) if entry.labels else {},
                "insert_id": entry.insert_id or "",
                "trace": entry.trace or "",
                "span_id": entry.span_id or "",
            }
        )
    return results


# ── Tool Definitions ────────────────────────────────────────────────


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Return the tools provided by this MCP server."""
    return [
        Tool(
            name="query_logs",
            description=(
                "Query Google Cloud Logging with a filter expression. "
                "Returns structured log entries matching the filter within the "
                "specified time window."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "GCP project ID to query logs from",
                    },
                    "filter_expr": {
                        "type": "string",
                        "description": (
                            "Cloud Logging filter expression, e.g. "
                            "'severity>=ERROR AND resource.type=\"k8s_container\"'"
                        ),
                    },
                    "hours_back": {
                        "type": "integer",
                        "description": "How many hours back to search (default: 1)",
                        "default": 1,
                    },
                    "max_entries": {
                        "type": "integer",
                        "description": "Maximum number of entries to return (default: 50)",
                        "default": 50,
                    },
                },
                "required": ["project_id", "filter_expr"],
            },
        ),
        Tool(
            name="detect_error_patterns",
            description=(
                "Analyse recent error logs and identify recurring patterns. "
                "Groups errors by message similarity and returns the top patterns "
                "with frequency counts and sample entries."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "GCP project ID",
                    },
                    "resource_type": {
                        "type": "string",
                        "description": "Resource type to filter, e.g. 'k8s_container'",
                        "default": "k8s_container",
                    },
                    "hours_back": {
                        "type": "integer",
                        "description": "How many hours back to analyse (default: 2)",
                        "default": 2,
                    },
                    "min_occurrences": {
                        "type": "integer",
                        "description": "Minimum occurrences to count as a pattern (default: 3)",
                        "default": 3,
                    },
                },
                "required": ["project_id"],
            },
        ),
        Tool(
            name="get_log_volume_anomalies",
            description=(
                "Detect anomalies in log volume over time. Compares recent log "
                "rates against baseline to identify sudden spikes in errors or "
                "specific log patterns."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "GCP project ID",
                    },
                    "severity": {
                        "type": "string",
                        "description": "Minimum severity to track (default: ERROR)",
                        "default": "ERROR",
                    },
                    "baseline_hours": {
                        "type": "integer",
                        "description": "Hours for baseline period (default: 24)",
                        "default": 24,
                    },
                    "recent_minutes": {
                        "type": "integer",
                        "description": "Minutes for recent window (default: 30)",
                        "default": 30,
                    },
                },
                "required": ["project_id"],
            },
        ),
        Tool(
            name="correlate_logs_by_trace",
            description=(
                "Given a trace ID, retrieve all correlated log entries across "
                "services. Useful for tracing a request path during an incident."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "GCP project ID",
                    },
                    "trace_id": {
                        "type": "string",
                        "description": "The trace ID to correlate logs for",
                    },
                    "hours_back": {
                        "type": "integer",
                        "description": "How many hours back to search (default: 4)",
                        "default": 4,
                    },
                },
                "required": ["project_id", "trace_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Execute a tool call."""
    try:
        if name == "query_logs":
            return await _query_logs(**arguments)
        elif name == "detect_error_patterns":
            return await _detect_error_patterns(**arguments)
        elif name == "get_log_volume_anomalies":
            return await _get_log_volume_anomalies(**arguments)
        elif name == "correlate_logs_by_trace":
            return await _correlate_logs_by_trace(**arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        logger.exception(f"Error executing tool {name}")
        return [TextContent(type="text", text=f"Error: {e!s}")]


# ── Tool Implementations ───────────────────────────────────────────


async def _query_logs(
    project_id: str,
    filter_expr: str,
    hours_back: int = 1,
    max_entries: int = 50,
) -> list[TextContent]:
    """Query Cloud Logging and return matching entries."""
    client = _build_client(project_id)
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours_back)

    time_filter = f'timestamp>="{start.isoformat()}" AND timestamp<="{now.isoformat()}"'
    full_filter = f"{filter_expr} AND {time_filter}"

    entries = list(client.list_entries(filter_=full_filter, max_results=max_entries))
    formatted = _format_entries(entries)

    result = {
        "query": full_filter,
        "total_entries": len(formatted),
        "time_range": {"start": start.isoformat(), "end": now.isoformat()},
        "entries": formatted,
    }
    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def _detect_error_patterns(
    project_id: str,
    resource_type: str = "k8s_container",
    hours_back: int = 2,
    min_occurrences: int = 3,
) -> list[TextContent]:
    """Detect recurring error patterns in logs."""
    client = _build_client(project_id)
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours_back)

    filter_expr = (
        f'severity>=ERROR AND resource.type="{resource_type}" '
        f'AND timestamp>="{start.isoformat()}"'
    )

    entries = list(client.list_entries(filter_=filter_expr, max_results=500))

    # Group by message similarity (simplified: first 100 chars)
    patterns: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        msg = str(entry.payload)[:100] if entry.payload else "unknown"
        key = msg.strip()
        if key not in patterns:
            patterns[key] = []
        patterns[key].append(
            {
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else "",
                "full_message": str(entry.payload)[:500],
                "resource_labels": dict(entry.resource.labels) if entry.resource else {},
            }
        )

    # Filter to patterns meeting minimum occurrence threshold
    significant_patterns = []
    for pattern_key, occurrences in sorted(
        patterns.items(), key=lambda x: len(x[1]), reverse=True
    ):
        if len(occurrences) >= min_occurrences:
            significant_patterns.append(
                {
                    "pattern": pattern_key,
                    "count": len(occurrences),
                    "first_seen": occurrences[-1]["timestamp"],
                    "last_seen": occurrences[0]["timestamp"],
                    "sample_entries": occurrences[:3],
                }
            )

    result = {
        "total_errors": len(entries),
        "unique_patterns": len(significant_patterns),
        "time_range": {"start": start.isoformat(), "end": now.isoformat()},
        "patterns": significant_patterns[:10],  # Top 10
    }
    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def _get_log_volume_anomalies(
    project_id: str,
    severity: str = "ERROR",
    baseline_hours: int = 24,
    recent_minutes: int = 30,
) -> list[TextContent]:
    """Detect anomalies in log volume."""
    client = _build_client(project_id)
    now = datetime.now(timezone.utc)

    # Get baseline volume
    baseline_start = now - timedelta(hours=baseline_hours)
    baseline_filter = f'severity>={severity} AND timestamp>="{baseline_start.isoformat()}"'
    baseline_entries = list(client.list_entries(filter_=baseline_filter, max_results=1000))

    baseline_rate = len(baseline_entries) / baseline_hours if baseline_hours > 0 else 0

    # Get recent volume
    recent_start = now - timedelta(minutes=recent_minutes)
    recent_filter = f'severity>={severity} AND timestamp>="{recent_start.isoformat()}"'
    recent_entries = list(client.list_entries(filter_=recent_filter, max_results=500))

    recent_rate = len(recent_entries) / (recent_minutes / 60) if recent_minutes > 0 else 0

    # Determine if anomalous (> 3x baseline rate)
    is_anomalous = recent_rate > (baseline_rate * 3) if baseline_rate > 0 else len(recent_entries) > 10
    anomaly_factor = round(recent_rate / baseline_rate, 2) if baseline_rate > 0 else float("inf")

    result = {
        "is_anomalous": is_anomalous,
        "anomaly_factor": anomaly_factor,
        "baseline": {
            "period_hours": baseline_hours,
            "total_entries": len(baseline_entries),
            "rate_per_hour": round(baseline_rate, 2),
        },
        "recent": {
            "period_minutes": recent_minutes,
            "total_entries": len(recent_entries),
            "rate_per_hour": round(recent_rate, 2),
        },
        "severity_filter": severity,
    }
    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def _correlate_logs_by_trace(
    project_id: str,
    trace_id: str,
    hours_back: int = 4,
) -> list[TextContent]:
    """Correlate logs by trace ID across services."""
    client = _build_client(project_id)
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours_back)

    filter_expr = (
        f'trace="projects/{project_id}/traces/{trace_id}" '
        f'AND timestamp>="{start.isoformat()}"'
    )

    entries = list(client.list_entries(filter_=filter_expr, max_results=200))
    formatted = _format_entries(entries)

    # Group by service/resource
    services: dict[str, list[dict[str, Any]]] = {}
    for entry in formatted:
        svc = entry.get("resource_labels", {}).get("container_name", "unknown")
        if svc not in services:
            services[svc] = []
        services[svc].append(entry)

    result = {
        "trace_id": trace_id,
        "total_entries": len(formatted),
        "services_involved": list(services.keys()),
        "entries_by_service": {svc: entries[:10] for svc, entries in services.items()},
        "time_range": {"start": start.isoformat(), "end": now.isoformat()},
    }
    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


# ── SSE Transport & ASGI App ───────────────────────────────────────


def create_app(server_instance: Server | None = None) -> Starlette:
    """Create the Starlette ASGI application with SSE transport."""
    srv = server_instance or server
    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await srv.run(
                streams[0], streams[1], srv.create_initialization_options()
            )

    return Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )


if __name__ == "__main__":
    import uvicorn

    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8001)
