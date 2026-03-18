"""
Notifications MCP Server for TheNightOps.

Provides multi-channel alerting capabilities so TheNightOps can notify
teams and stakeholders through their preferred channel:
- Email (SMTP) — for formal RCA reports and stakeholder notifications
- Telegram — for instant team alerts with bot integration
- WhatsApp — for on-call escalation via WhatsApp Business API

Each channel is independently configurable and can be enabled/disabled.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.routing import Mount, Route

logger = logging.getLogger(__name__)

server = Server("nightops-notifications")


# ── Server-side credential helpers ──────────────────────────────────
# Secrets are loaded from environment variables at call time — never
# accepted as tool parameters to prevent exposure to the LLM.


def _get_smtp_config() -> dict[str, Any]:
    host = os.environ.get("SMTP_HOST", "")
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    from_email = os.environ.get("SMTP_FROM_EMAIL", user)
    port = int(os.environ.get("SMTP_PORT", "587"))
    if not all([host, user, password]):
        raise ValueError(
            "SMTP credentials not configured. Set SMTP_HOST, SMTP_USER, "
            "and SMTP_PASSWORD environment variables."
        )
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "from_email": from_email,
    }


def _get_telegram_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is not set.")
    return token


def _get_whatsapp_config() -> dict[str, str]:
    api_url = os.environ.get("WHATSAPP_API_URL", "")
    api_token = os.environ.get("WHATSAPP_API_TOKEN", "")
    if not all([api_url, api_token]):
        raise ValueError(
            "WhatsApp credentials not configured. Set WHATSAPP_API_URL "
            "and WHATSAPP_API_TOKEN environment variables."
        )
    return {"api_url": api_url, "api_token": api_token}


# ── Tool Definitions ────────────────────────────────────────────────


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        # ── Email Tools ──────────────────────────────────────────
        Tool(
            name="send_email_alert",
            description=(
                "Send an incident alert or RCA report via email using SMTP. "
                "Supports HTML formatting for professional stakeholder communications. "
                "Can send to multiple recipients with CC/BCC support."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "to_emails": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of recipient email addresses",
                    },
                    "cc_emails": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "CC recipients",
                        "default": [],
                    },
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body_html": {
                        "type": "string",
                        "description": "Email body in HTML format",
                    },
                    "body_text": {
                        "type": "string",
                        "description": "Plain text fallback for the email body",
                        "default": "",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["high", "normal", "low"],
                        "description": "Email priority header",
                        "default": "high",
                    },
                },
                "required": ["to_emails", "subject", "body_html"],
            },
        ),
        Tool(
            name="send_email_rca",
            description=(
                "Send a formatted Root Cause Analysis report via email. "
                "Automatically formats the RCA into a professional HTML email "
                "with timeline, root cause, impact, and action items."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "to_emails": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "incident_title": {"type": "string"},
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                    "root_cause": {"type": "string"},
                    "impact": {"type": "string"},
                    "timeline_summary": {"type": "string", "default": ""},
                    "action_items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [],
                    },
                },
                "required": ["to_emails", "incident_title", "severity", "root_cause", "impact"],
            },
        ),
        # ── Telegram Tools ───────────────────────────────────────
        Tool(
            name="send_telegram_alert",
            description=(
                "Send an instant incident alert to a Telegram chat or group "
                "using a Telegram Bot. Ideal for on-call team notifications "
                "that need immediate attention. Supports Markdown formatting."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": "string",
                        "description": "Telegram chat ID or group ID (use @channel for public channels)",
                    },
                    "message": {
                        "type": "string",
                        "description": "Alert message (supports Markdown formatting)",
                    },
                    "parse_mode": {
                        "type": "string",
                        "enum": ["Markdown", "HTML"],
                        "default": "Markdown",
                    },
                    "disable_notification": {
                        "type": "boolean",
                        "description": "Send silently (no notification sound)",
                        "default": False,
                    },
                },
                "required": ["chat_id", "message"],
            },
        ),
        Tool(
            name="send_telegram_incident_card",
            description=(
                "Send a richly formatted incident card to Telegram with "
                "severity indicators, status, and action buttons. "
                "More structured than a plain text alert."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "incident_title": {"type": "string"},
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                    "status": {
                        "type": "string",
                        "enum": ["investigating", "identified", "monitoring", "resolved"],
                    },
                    "summary": {"type": "string"},
                    "affected_services": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [],
                    },
                },
                "required": ["chat_id", "incident_title", "severity", "status", "summary"],
            },
        ),
        # ── WhatsApp Tools ───────────────────────────────────────
        Tool(
            name="send_whatsapp_alert",
            description=(
                "Send an incident alert via WhatsApp Business API. "
                "Ideal for critical on-call escalations when engineers "
                "need to be reached immediately on their personal device."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "phone_number": {
                        "type": "string",
                        "description": "Recipient phone number in international format (e.g. +1234567890)",
                    },
                    "template_name": {
                        "type": "string",
                        "description": "WhatsApp message template name (must be pre-approved)",
                        "default": "incident_alert",
                    },
                    "template_params": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Template parameter values (incident title, severity, summary)",
                        "default": [],
                    },
                    "message": {
                        "type": "string",
                        "description": "Plain text message (used if no template specified)",
                        "default": "",
                    },
                },
                "required": ["phone_number"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        if name == "send_email_alert":
            return await _send_email_alert(**arguments)
        elif name == "send_email_rca":
            return await _send_email_rca(**arguments)
        elif name == "send_telegram_alert":
            return await _send_telegram_alert(**arguments)
        elif name == "send_telegram_incident_card":
            return await _send_telegram_incident_card(**arguments)
        elif name == "send_whatsapp_alert":
            return await _send_whatsapp_alert(**arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        logger.exception(f"Error executing tool {name}")
        return [TextContent(type="text", text=f"Error: {e!s}")]


# ── Email Implementation ───────────────────────────────────────────


def _severity_color(severity: str) -> str:
    return {
        "critical": "#dc2626",
        "high": "#ea580c",
        "medium": "#ca8a04",
        "low": "#16a34a",
    }.get(severity, "#6b7280")


def _send_smtp_blocking(
    smtp_cfg: dict[str, Any],
    msg: MIMEMultipart,
    all_recipients: list[str],
) -> None:
    """Synchronous SMTP send — intended to be called via asyncio.to_thread."""
    with smtplib.SMTP(smtp_cfg["host"], smtp_cfg["port"]) as srv:
        srv.starttls()
        srv.login(smtp_cfg["user"], smtp_cfg["password"])
        srv.send_message(msg, to_addrs=all_recipients)


async def _send_email_alert(
    to_emails: list[str],
    subject: str,
    body_html: str,
    cc_emails: list[str] | None = None,
    body_text: str = "",
    priority: str = "high",
) -> list[TextContent]:
    """Send an email alert via SMTP (credentials loaded from env)."""
    smtp_cfg = _get_smtp_config()

    msg = MIMEMultipart("alternative")
    msg["From"] = smtp_cfg["from_email"]
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = subject

    if cc_emails:
        msg["Cc"] = ", ".join(cc_emails)

    # Priority header
    priority_map = {"high": "1", "normal": "3", "low": "5"}
    msg["X-Priority"] = priority_map.get(priority, "3")

    if body_text:
        msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    all_recipients = to_emails + (cc_emails or [])

    # Run blocking SMTP in a thread to avoid stalling the async event loop
    await asyncio.to_thread(_send_smtp_blocking, smtp_cfg, msg, all_recipients)

    logger.info("Email sent to %d recipients: %s", len(all_recipients), subject)

    return [
        TextContent(
            type="text",
            text=json.dumps({
                "sent": True,
                "recipients": len(all_recipients),
                "subject": subject,
            }),
        )
    ]


async def _send_email_rca(
    to_emails: list[str],
    incident_title: str,
    severity: str,
    root_cause: str,
    impact: str,
    timeline_summary: str = "",
    action_items: list[str] | None = None,
) -> list[TextContent]:
    """Send a formatted RCA report via email."""
    color = _severity_color(severity)

    action_html = ""
    if action_items:
        items = "".join(f"<li>{item}</li>" for item in action_items)
        action_html = f"<h3>Action Items</h3><ol>{items}</ol>"

    timeline_html = ""
    if timeline_summary:
        timeline_html = f"<h3>Timeline</h3><p>{timeline_summary}</p>"

    body_html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: {color}; color: white; padding: 16px 24px; border-radius: 8px 8px 0 0;">
            <h2 style="margin: 0;">RCA: {incident_title}</h2>
            <p style="margin: 4px 0 0 0; opacity: 0.9;">Severity: {severity.upper()}</p>
        </div>
        <div style="padding: 24px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 8px 8px;">
            <h3>Root Cause</h3>
            <p>{root_cause}</p>

            <h3>Impact</h3>
            <p>{impact}</p>

            {timeline_html}
            {action_html}

            <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 24px 0;">
            <p style="color: #6b7280; font-size: 12px;">
                Generated by TheNightOps — Autonomous SRE Agent
            </p>
        </div>
    </div>
    """

    return await _send_email_alert(
        to_emails=to_emails,
        subject=f"[RCA] [{severity.upper()}] {incident_title}",
        body_html=body_html,
        priority="high" if severity in ("critical", "high") else "normal",
    )


# ── Telegram Implementation ───────────────────────────────────────

TELEGRAM_API_BASE = "https://api.telegram.org"


async def _send_telegram_alert(
    chat_id: str,
    message: str,
    parse_mode: str = "Markdown",
    disable_notification: bool = False,
) -> list[TextContent]:
    """Send a text alert via Telegram Bot API (token loaded from env)."""
    bot_token = _get_telegram_token()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": parse_mode,
                "disable_notification": disable_notification,
            },
        )
        data = resp.json()

    if not data.get("ok"):
        return [TextContent(type="text", text=f"Telegram error: {data.get('description', 'unknown')}")]

    return [
        TextContent(
            type="text",
            text=json.dumps({
                "sent": True,
                "chat_id": chat_id,
                "message_id": data.get("result", {}).get("message_id"),
            }),
        )
    ]


async def _send_telegram_incident_card(
    chat_id: str,
    incident_title: str,
    severity: str,
    status: str,
    summary: str,
    affected_services: list[str] | None = None,
) -> list[TextContent]:
    """Send a formatted incident card to Telegram."""
    severity_emoji = {"critical": "\U0001f534", "high": "\U0001f7e0", "medium": "\U0001f7e1", "low": "\U0001f7e2"}.get(severity, "\u26aa")
    status_emoji = {"investigating": "\U0001f50d", "identified": "\U0001f3af", "monitoring": "\U0001f440", "resolved": "\u2705"}.get(status, "\u2753")

    services_text = ""
    if affected_services:
        services_text = f"\n*Affected Services:* {', '.join(affected_services)}"

    message = (
        f"{severity_emoji} *INCIDENT: {incident_title}*\n"
        f"\n"
        f"*Severity:* {severity.upper()}\n"
        f"*Status:* {status_emoji} {status.title()}\n"
        f"{services_text}\n"
        f"\n"
        f"*Summary:*\n{summary}\n"
        f"\n"
        f"_Sent by TheNightOps_"
    )

    return await _send_telegram_alert(
        chat_id=chat_id,
        message=message,
        parse_mode="Markdown",
        disable_notification=False,
    )


# ── WhatsApp Implementation ───────────────────────────────────────


async def _send_whatsapp_alert(
    phone_number: str,
    template_name: str = "incident_alert",
    template_params: list[str] | None = None,
    message: str = "",
) -> list[TextContent]:
    """Send an alert via WhatsApp Business API (credentials loaded from env)."""
    wa_cfg = _get_whatsapp_config()
    api_url = wa_cfg["api_url"]
    headers = {
        "Authorization": f"Bearer {wa_cfg['api_token']}",
        "Content-Type": "application/json",
    }

    if template_name and template_params:
        # Use approved message template
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": "en"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": param}
                            for param in template_params
                        ],
                    }
                ],
            },
        }
    elif message:
        # Send plain text message (only works within 24-hour window)
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": {"body": message},
        }
    else:
        return [TextContent(type="text", text="Error: Either template_params or message required")]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{api_url}/messages",
            headers=headers,
            json=payload,
        )
        data = resp.json()

    if resp.status_code >= 400:
        return [TextContent(type="text", text=f"WhatsApp API error: {json.dumps(data)}")]

    return [
        TextContent(
            type="text",
            text=json.dumps({
                "sent": True,
                "phone_number": phone_number,
                "message_id": data.get("messages", [{}])[0].get("id", ""),
            }),
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
    uvicorn.run(app, host="0.0.0.0", port=8005)
