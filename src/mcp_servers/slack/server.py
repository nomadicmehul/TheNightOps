"""
Slack MCP Server for TheNightOps.

Provides tools to post incident updates, send RCA summaries,
notify stakeholders, and manage incident communication threads.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.routing import Mount, Route

logger = logging.getLogger(__name__)

server = Server("nightops-slack")

SLACK_API_BASE = "https://slack.com/api"

# Load bot token from environment at startup — never accept from tool calls
_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")


def _get_bot_token() -> str:
    """Get the Slack bot token from environment."""
    token = _BOT_TOKEN or os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise ValueError(
            "SLACK_BOT_TOKEN environment variable is not set. "
            "Configure it in config/.env before starting the Slack MCP server."
        )
    return token


def _get_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_bot_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="post_incident_update",
            description=(
                "Post an incident update to a Slack channel. Formats the message "
                "with severity, status, and summary using Slack Block Kit."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Channel name or ID (e.g. '#incidents')",
                    },
                    "incident_title": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "status": {
                        "type": "string",
                        "enum": ["investigating", "identified", "monitoring", "resolved"],
                    },
                    "summary": {"type": "string", "description": "Brief update summary"},
                    "thread_ts": {
                        "type": "string",
                        "description": "Thread timestamp to reply to (for updates)",
                        "default": "",
                    },
                },
                "required": ["channel", "incident_title", "severity", "status", "summary"],
            },
        ),
        Tool(
            name="post_rca_summary",
            description=(
                "Post a formatted Root Cause Analysis summary to a Slack channel. "
                "Includes timeline, root cause, impact, and action items."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "incident_title": {"type": "string"},
                    "root_cause": {"type": "string"},
                    "impact": {"type": "string"},
                    "timeline_summary": {"type": "string"},
                    "action_items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of action items",
                    },
                    "thread_ts": {"type": "string", "default": ""},
                },
                "required": ["channel", "incident_title", "root_cause", "impact"],
            },
        ),
        Tool(
            name="notify_stakeholders",
            description=(
                "Send a concise business-impact notification to stakeholders. "
                "Uses simple language suitable for non-technical audiences."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "incident_title": {"type": "string"},
                    "business_impact": {"type": "string"},
                    "current_status": {"type": "string"},
                    "estimated_resolution": {
                        "type": "string",
                        "description": "Estimated time to resolution",
                        "default": "Under investigation",
                    },
                },
                "required": ["channel", "incident_title", "business_impact", "current_status"],
            },
        ),
        Tool(
            name="create_incident_channel",
            description=(
                "Create a dedicated Slack channel for incident coordination "
                "and invite relevant team members."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "incident_id": {"type": "string"},
                    "incident_title": {"type": "string"},
                    "invite_users": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "User IDs to invite",
                        "default": [],
                    },
                },
                "required": ["incident_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        if name == "post_incident_update":
            return await _post_incident_update(**arguments)
        elif name == "post_rca_summary":
            return await _post_rca_summary(**arguments)
        elif name == "notify_stakeholders":
            return await _notify_stakeholders(**arguments)
        elif name == "create_incident_channel":
            return await _create_incident_channel(**arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        logger.exception(f"Error executing tool {name}")
        return [TextContent(type="text", text=f"Error: {e!s}")]


def _severity_emoji(severity: str) -> str:
    return {
        "critical": "🔴",
        "high": "🟠",
        "medium": "🟡",
        "low": "🟢",
    }.get(severity, "⚪")


def _status_emoji(status: str) -> str:
    return {
        "investigating": "🔍",
        "identified": "🎯",
        "monitoring": "👀",
        "resolved": "✅",
    }.get(status, "❓")


async def _post_incident_update(
    channel: str,
    incident_title: str,
    severity: str,
    status: str,
    summary: str,
    thread_ts: str = "",
) -> list[TextContent]:
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{_severity_emoji(severity)} Incident: {incident_title}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Severity:* {severity.upper()}"},
                {"type": "mrkdwn", "text": f"*Status:* {_status_emoji(status)} {status.title()}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Update:*\n{summary}"},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "🤖 _Posted by TheNightOps — Autonomous SRE Agent_"},
            ],
        },
    ]

    payload: dict[str, Any] = {"channel": channel, "blocks": blocks, "text": f"Incident: {incident_title}"}
    if thread_ts:
        payload["thread_ts"] = thread_ts

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{SLACK_API_BASE}/chat.postMessage",
            headers=_get_headers(),
            json=payload,
        )
        data = resp.json()

    if not data.get("ok"):
        return [TextContent(type="text", text=f"Slack error: {data.get('error', 'unknown')}")]

    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "posted": True,
                    "channel": data.get("channel"),
                    "ts": data.get("ts"),
                    "thread_ts": data.get("ts") if not thread_ts else thread_ts,
                }
            ),
        )
    ]


async def _post_rca_summary(
    channel: str,
    incident_title: str,
    root_cause: str,
    impact: str,
    timeline_summary: str = "",
    action_items: list[str] | None = None,
    thread_ts: str = "",
) -> list[TextContent]:
    action_text = ""
    if action_items:
        action_text = "\n".join(f"• {item}" for item in action_items)

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📋 RCA: {incident_title}"},
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🔍 Root Cause:*\n{root_cause}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*💥 Impact:*\n{impact}"},
        },
    ]

    if timeline_summary:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*⏱️ Timeline:*\n{timeline_summary}"},
            }
        )

    if action_text:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*📌 Action Items:*\n{action_text}"},
            }
        )

    blocks.append(
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "🤖 _RCA generated by TheNightOps_"},
            ],
        }
    )

    payload: dict[str, Any] = {"channel": channel, "blocks": blocks, "text": f"RCA: {incident_title}"}
    if thread_ts:
        payload["thread_ts"] = thread_ts

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{SLACK_API_BASE}/chat.postMessage",
            headers=_get_headers(),
            json=payload,
        )
        data = resp.json()

    return [TextContent(type="text", text=json.dumps({"posted": data.get("ok", False), "ts": data.get("ts")}))]


async def _notify_stakeholders(
    channel: str,
    incident_title: str,
    business_impact: str,
    current_status: str,
    estimated_resolution: str = "Under investigation",
) -> list[TextContent]:
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"⚡ Service Status Update"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Incident:* {incident_title}\n\n"
                    f"*What's happening:* {business_impact}\n\n"
                    f"*Current status:* {current_status}\n\n"
                    f"*Estimated resolution:* {estimated_resolution}"
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "_Our team is actively working on this. We'll provide updates as the situation evolves._"},
            ],
        },
    ]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{SLACK_API_BASE}/chat.postMessage",
            headers=_get_headers(),
            json={"channel": channel, "blocks": blocks, "text": f"Status: {incident_title}"},
        )
        data = resp.json()

    return [TextContent(type="text", text=json.dumps({"posted": data.get("ok", False), "ts": data.get("ts")}))]


async def _create_incident_channel(
    incident_id: str,
    incident_title: str = "",
    invite_users: list[str] | None = None,
) -> list[TextContent]:
    channel_name = f"inc-{incident_id.lower().replace(' ', '-')[:60]}"

    async with httpx.AsyncClient(timeout=30) as client:
        # Create channel
        resp = await client.post(
            f"{SLACK_API_BASE}/conversations.create",
            headers=_get_headers(),
            json={"name": channel_name, "is_private": False},
        )
        data = resp.json()

        if not data.get("ok"):
            return [TextContent(type="text", text=f"Error creating channel: {data.get('error')}")]

        channel_id = data["channel"]["id"]

        # Set topic
        if incident_title:
            await client.post(
                f"{SLACK_API_BASE}/conversations.setTopic",
                headers=_get_headers(),
                json={"channel": channel_id, "topic": f"Incident: {incident_title}"},
            )

        # Invite users
        if invite_users:
            await client.post(
                f"{SLACK_API_BASE}/conversations.invite",
                headers=_get_headers(),
                json={"channel": channel_id, "users": ",".join(invite_users)},
            )

    return [
        TextContent(
            type="text",
            text=json.dumps(
                {"channel_id": channel_id, "channel_name": channel_name, "created": True}
            ),
        )
    ]


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
    uvicorn.run(app, host="0.0.0.0", port=8004)
