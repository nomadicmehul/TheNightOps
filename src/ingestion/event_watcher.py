"""
Kubernetes Event Watcher for TheNightOps.

Watches Kubernetes Warning events in real-time and automatically
creates incidents for actionable events like OOMKilled, CrashLoopBackOff,
FailedScheduling, etc.

This is the "always-on eyes" that make the agent truly autonomous —
it doesn't need an external alerting system to detect problems.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from nightops.core.config import EventWatcherConfig
from nightops.core.models import Incident, Severity
from nightops.ingestion.deduplication import AlertDeduplicator

logger = logging.getLogger(__name__)

# Map K8s event reasons to severity levels
REASON_SEVERITY_MAP = {
    "OOMKilled": Severity.CRITICAL,
    "CrashLoopBackOff": Severity.CRITICAL,
    "FailedScheduling": Severity.HIGH,
    "Unhealthy": Severity.HIGH,
    "BackOff": Severity.HIGH,
    "FailedMount": Severity.MEDIUM,
    "FailedAttachVolume": Severity.MEDIUM,
    "Evicted": Severity.HIGH,
    "NodeNotReady": Severity.CRITICAL,
    "FailedCreate": Severity.HIGH,
}


class EventWatcher:
    """Watches Kubernetes events and creates incidents for actionable warnings."""

    def __init__(
        self,
        config: EventWatcherConfig,
        deduplicator: AlertDeduplicator,
        on_new_incident: Any = None,
    ):
        self.config = config
        self.deduplicator = deduplicator
        self.on_new_incident = on_new_incident
        self._running = False
        self._watch_task: Optional[asyncio.Task] = None
        self._events_processed = 0

    async def start(self) -> None:
        """Start watching Kubernetes events."""
        if not self.config.enabled:
            logger.info("Event watcher disabled in configuration")
            return

        self._running = True
        self._watch_task = asyncio.create_task(self._watch_loop())
        logger.info(
            "Event watcher started — watching namespaces: %s, reasons: %s",
            self.config.namespaces,
            self.config.watch_reasons,
        )

    async def stop(self) -> None:
        """Stop watching Kubernetes events."""
        self._running = False
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
        logger.info("Event watcher stopped (processed %d events)", self._events_processed)

    async def _watch_loop(self) -> None:
        """Main watch loop — connects to the Kubernetes API and streams events."""
        try:
            from kubernetes import client, config, watch as k8s_watch
        except ImportError:
            logger.error("kubernetes package not installed — event watcher cannot start")
            return

        # Load kubeconfig
        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes config")
        except config.ConfigException:
            try:
                config.load_kube_config()
                logger.info("Loaded kubeconfig from default location")
            except config.ConfigException:
                logger.error("Cannot load Kubernetes config — event watcher disabled")
                return

        v1 = client.CoreV1Api()
        w = k8s_watch.Watch()

        while self._running:
            try:
                for namespace in self.config.namespaces:
                    logger.info("Watching events in namespace: %s", namespace)
                    kwargs = {"namespace": namespace, "timeout_seconds": 300}

                    # Run the blocking watch in a thread to keep async compatibility
                    stream = await asyncio.to_thread(
                        lambda: list(w.stream(v1.list_namespaced_event, **kwargs))
                    )

                    for event_data in stream:
                        if not self._running:
                            break
                        await self._handle_event(event_data)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Event watcher error, retrying in 10s")
                await asyncio.sleep(10)

    async def _handle_event(self, event_data: dict) -> None:
        """Process a single Kubernetes event."""
        event_type = event_data.get("type", "")
        obj = event_data.get("object")
        if obj is None:
            return

        reason = getattr(obj, "reason", "") or ""
        message = getattr(obj, "message", "") or ""
        event_k8s_type = getattr(obj, "type", "Normal")
        involved_object = getattr(obj, "involved_object", None)

        # Filter: only Warning events with watched reasons
        if event_k8s_type != "Warning":
            return
        if reason not in self.config.watch_reasons:
            return

        self._events_processed += 1

        # Extract pod/resource info
        pod_name = ""
        namespace = ""
        resource_kind = ""
        if involved_object:
            pod_name = getattr(involved_object, "name", "")
            namespace = getattr(involved_object, "namespace", "")
            resource_kind = getattr(involved_object, "kind", "")

        severity = REASON_SEVERITY_MAP.get(reason, Severity.MEDIUM)

        # Build fingerprint for dedup
        fingerprint_key = f"k8s:{namespace}:{reason}:{pod_name}".lower()
        import hashlib
        fingerprint = hashlib.sha256(fingerprint_key.encode()).hexdigest()[:16]

        # Check dedup using a lightweight WebhookAlert wrapper
        from nightops.core.models import WebhookAlert
        alert = WebhookAlert(
            source="event_watcher",
            alert_name=f"K8s {reason}: {pod_name}",
            severity=severity,
            service=pod_name.rsplit("-", 2)[0] if pod_name else "",  # Strip pod hash
            namespace=namespace,
            description=message,
            fingerprint=fingerprint,
        )

        is_new = self.deduplicator.check_and_add(alert)
        if not is_new:
            return

        # Create incident
        incident = Incident(
            id=f"inc-{uuid.uuid4().hex[:8]}",
            title=f"{severity.value.upper()}: {reason} on {resource_kind}/{pod_name}",
            description=(
                f"Kubernetes {reason} event detected.\n\n"
                f"**Resource:** {resource_kind}/{pod_name}\n"
                f"**Namespace:** {namespace}\n"
                f"**Message:** {message}"
            ),
            severity=severity,
            service_name=pod_name.rsplit("-", 2)[0] if pod_name else "",
            namespace=namespace,
            fingerprint=fingerprint,
            source="event_watcher",
        )

        logger.info(
            "Event watcher detected: %s in %s/%s — creating incident %s",
            reason, namespace, pod_name, incident.id,
        )

        if self.on_new_incident:
            try:
                await self.on_new_incident(incident)
            except Exception:
                logger.exception("Error dispatching incident %s from event watcher", incident.id)
