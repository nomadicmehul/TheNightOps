"""
Webhook Receiver for TheNightOps.

Receives alerts from Grafana, Alertmanager, PagerDuty, and custom sources,
normalizes them into a unified format, deduplicates, and triggers investigations.

Endpoints:
    POST /api/v1/webhooks/grafana       — Grafana alerting webhook
    POST /api/v1/webhooks/alertmanager  — Prometheus Alertmanager webhook
    POST /api/v1/webhooks/pagerduty     — PagerDuty webhook
    POST /api/v1/webhooks/generic       — Generic JSON alert
    GET  /api/v1/webhooks/health        — Health check
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from nightops.core.models import Incident, Severity, WebhookAlert
from nightops.ingestion.deduplication import AlertDeduplicator

logger = logging.getLogger(__name__)


class WebhookReceiver:
    """Receives, normalizes, and dispatches alerts from external systems."""

    def __init__(
        self,
        deduplicator: AlertDeduplicator,
        on_new_incident: Any = None,
    ):
        self.deduplicator = deduplicator
        self.on_new_incident = on_new_incident  # async callback
        self._received_count = 0

    # ── Grafana Webhook ───────────────────────────────────────────

    def normalize_grafana(self, payload: dict) -> list[WebhookAlert]:
        """Normalize Grafana alerting webhook payload."""
        alerts = []
        for alert in payload.get("alerts", [payload]):
            labels = alert.get("labels", {})
            annotations = alert.get("annotations", {})
            severity_str = labels.get("severity", annotations.get("severity", "high"))
            try:
                severity = Severity(severity_str.lower())
            except ValueError:
                severity = Severity.HIGH

            alerts.append(WebhookAlert(
                source="grafana",
                alert_name=labels.get("alertname", alert.get("title", "Unknown Alert")),
                status=alert.get("status", "firing"),
                severity=severity,
                service=labels.get("service", labels.get("job", "")),
                namespace=labels.get("namespace", ""),
                cluster=labels.get("cluster", ""),
                environment=labels.get("environment", labels.get("env", "")),
                description=annotations.get("description", annotations.get("summary", "")),
                labels=labels,
                annotations=annotations,
                starts_at=_parse_datetime(alert.get("startsAt")),
                ends_at=_parse_datetime(alert.get("endsAt")),
                generator_url=alert.get("generatorURL", ""),
                fingerprint=alert.get("fingerprint", ""),
                raw_payload=alert,
            ))
        return alerts

    # ── Alertmanager Webhook ──────────────────────────────────────

    def normalize_alertmanager(self, payload: dict) -> list[WebhookAlert]:
        """Normalize Prometheus Alertmanager webhook payload."""
        alerts = []
        common_labels = payload.get("commonLabels", {})
        for alert in payload.get("alerts", []):
            labels = {**common_labels, **alert.get("labels", {})}
            annotations = alert.get("annotations", {})
            severity_str = labels.get("severity", "high")
            try:
                severity = Severity(severity_str.lower())
            except ValueError:
                severity = Severity.HIGH

            alerts.append(WebhookAlert(
                source="alertmanager",
                alert_name=labels.get("alertname", "Unknown"),
                status=alert.get("status", "firing"),
                severity=severity,
                service=labels.get("service", labels.get("job", "")),
                namespace=labels.get("namespace", ""),
                cluster=labels.get("cluster", ""),
                environment=labels.get("environment", ""),
                description=annotations.get("description", annotations.get("summary", "")),
                labels=labels,
                annotations=annotations,
                starts_at=_parse_datetime(alert.get("startsAt")),
                ends_at=_parse_datetime(alert.get("endsAt")),
                generator_url=alert.get("generatorURL", ""),
                fingerprint=alert.get("fingerprint", ""),
                raw_payload=alert,
            ))
        return alerts

    # ── PagerDuty Webhook ─────────────────────────────────────────

    def normalize_pagerduty(self, payload: dict) -> list[WebhookAlert]:
        """Normalize PagerDuty webhook (v3 Events API format)."""
        alerts = []
        for message in payload.get("messages", [payload]):
            event = message.get("event", message)
            incident_data = event.get("data", {})
            urgency = incident_data.get("urgency", "high")
            severity = Severity.CRITICAL if urgency == "high" else Severity.MEDIUM

            alerts.append(WebhookAlert(
                source="pagerduty",
                alert_name=incident_data.get("title", "PagerDuty Incident"),
                status="firing" if event.get("event_type", "").startswith("incident.triggered") else "resolved",
                severity=severity,
                service=incident_data.get("service", {}).get("name", ""),
                description=incident_data.get("description", incident_data.get("title", "")),
                labels={"urgency": urgency, "pd_id": str(incident_data.get("id", ""))},
                raw_payload=message,
            ))
        return alerts

    # ── Generic Webhook ───────────────────────────────────────────

    def normalize_generic(self, payload: dict) -> list[WebhookAlert]:
        """Normalize a generic JSON alert payload."""
        severity_str = payload.get("severity", "high")
        try:
            severity = Severity(severity_str.lower())
        except ValueError:
            severity = Severity.HIGH

        return [WebhookAlert(
            source="generic",
            alert_name=payload.get("title", payload.get("alert_name", "Unknown Alert")),
            status=payload.get("status", "firing"),
            severity=severity,
            service=payload.get("service", ""),
            namespace=payload.get("namespace", ""),
            cluster=payload.get("cluster", ""),
            environment=payload.get("environment", ""),
            description=payload.get("description", payload.get("message", "")),
            labels=payload.get("labels", {}),
            raw_payload=payload,
        )]

    # ── Alert Processing Pipeline ─────────────────────────────────

    async def process_alerts(self, alerts: list[WebhookAlert]) -> list[Incident]:
        """Process normalized alerts through deduplication and create incidents."""
        new_incidents = []

        for alert in alerts:
            if alert.status == "resolved":
                self.deduplicator.resolve(alert)
                continue

            # Compute fingerprint if not provided by source
            if not alert.fingerprint:
                alert.fingerprint = alert.compute_fingerprint()

            is_new = self.deduplicator.check_and_add(alert)
            if not is_new:
                logger.debug("Deduplicated alert: %s (fingerprint: %s)", alert.alert_name, alert.fingerprint)
                continue

            # Create incident from new alert
            incident = Incident(
                id=f"inc-{uuid.uuid4().hex[:8]}",
                title=f"{alert.severity.value.upper()}: {alert.alert_name}",
                description=alert.description or f"Alert '{alert.alert_name}' fired from {alert.source}",
                severity=alert.severity,
                service_name=alert.service,
                environment=alert.environment,
                cluster=alert.cluster,
                namespace=alert.namespace,
                fingerprint=alert.fingerprint,
                source=f"webhook:{alert.source}",
                alert_details={
                    "labels": alert.labels,
                    "annotations": alert.annotations,
                    "generator_url": alert.generator_url,
                },
            )
            new_incidents.append(incident)
            self._received_count += 1

            logger.info(
                "New incident created from %s webhook: %s (fingerprint: %s)",
                alert.source, incident.title, alert.fingerprint,
            )

            # Dispatch to investigation
            if self.on_new_incident:
                try:
                    await self.on_new_incident(incident)
                except Exception:
                    logger.exception("Error dispatching incident %s", incident.id)

        return new_incidents


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO datetime string, returning None on failure."""
    if not value or value == "0001-01-01T00:00:00Z":
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


# ── Starlette ASGI App ────────────────────────────────────────────


def create_webhook_app(
    deduplicator: AlertDeduplicator | None = None,
    on_new_incident: Any = None,
) -> Starlette:
    """Create the webhook receiver ASGI application."""
    if deduplicator is None:
        deduplicator = AlertDeduplicator()
    receiver = WebhookReceiver(deduplicator=deduplicator, on_new_incident=on_new_incident)

    async def grafana_webhook(request: Request) -> JSONResponse:
        payload = await request.json()
        alerts = receiver.normalize_grafana(payload)
        incidents = await receiver.process_alerts(alerts)
        return JSONResponse({
            "status": "ok",
            "alerts_received": len(alerts),
            "incidents_created": len(incidents),
            "incident_ids": [i.id for i in incidents],
        })

    async def alertmanager_webhook(request: Request) -> JSONResponse:
        payload = await request.json()
        alerts = receiver.normalize_alertmanager(payload)
        incidents = await receiver.process_alerts(alerts)
        return JSONResponse({
            "status": "ok",
            "alerts_received": len(alerts),
            "incidents_created": len(incidents),
            "incident_ids": [i.id for i in incidents],
        })

    async def pagerduty_webhook(request: Request) -> JSONResponse:
        payload = await request.json()
        alerts = receiver.normalize_pagerduty(payload)
        incidents = await receiver.process_alerts(alerts)
        return JSONResponse({
            "status": "ok",
            "alerts_received": len(alerts),
            "incidents_created": len(incidents),
            "incident_ids": [i.id for i in incidents],
        })

    async def generic_webhook(request: Request) -> JSONResponse:
        payload = await request.json()
        alerts = receiver.normalize_generic(payload)
        incidents = await receiver.process_alerts(alerts)
        return JSONResponse({
            "status": "ok",
            "alerts_received": len(alerts),
            "incidents_created": len(incidents),
            "incident_ids": [i.id for i in incidents],
        })

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({
            "status": "healthy",
            "total_received": receiver._received_count,
            "dedup_active_groups": len(receiver.deduplicator._groups),
        })

    return Starlette(
        routes=[
            Route("/api/v1/webhooks/grafana", grafana_webhook, methods=["POST"]),
            Route("/api/v1/webhooks/alertmanager", alertmanager_webhook, methods=["POST"]),
            Route("/api/v1/webhooks/pagerduty", pagerduty_webhook, methods=["POST"]),
            Route("/api/v1/webhooks/generic", generic_webhook, methods=["POST"]),
            Route("/api/v1/webhooks/health", health, methods=["GET"]),
        ],
    )
