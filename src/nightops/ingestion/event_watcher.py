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
from typing import Any

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
        self._watch_task: asyncio.Task | None = None
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
        """Main watch loop — connects to the Kubernetes API and streams events.

        Each configured namespace is watched by its own concurrent task so a
        quiet namespace never blocks events arriving in a busy one.
        """
        try:
            from kubernetes import client, config
            from kubernetes import watch as k8s_watch
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

        # Fan out: one watcher task per namespace, all running concurrently.
        tasks = [
            asyncio.create_task(self._watch_namespace(v1, k8s_watch, namespace))
            for namespace in self.config.namespaces
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _watch_namespace(self, v1: Any, k8s_watch: Any, namespace: str) -> None:
        """Watch a single namespace, processing events incrementally.

        Events are pulled from the blocking watch stream one at a time (each
        ``next()`` runs in a worker thread) so they are handled the moment they
        arrive instead of being buffered into multi-minute batches. When the
        server closes the stream after ``watch_timeout_seconds`` we reconnect.
        """
        sentinel = object()
        while self._running:
            w = k8s_watch.Watch()
            try:
                logger.info("Watching events in namespace: %s", namespace)
                stream_iter = iter(
                    w.stream(
                        v1.list_namespaced_event,
                        namespace=namespace,
                        timeout_seconds=self.config.watch_timeout_seconds,
                    )
                )
                while self._running:
                    # next(it, sentinel) avoids StopIteration crossing the
                    # thread/coroutine boundary; sentinel means the stream ended.
                    event_data = await asyncio.to_thread(next, stream_iter, sentinel)
                    if event_data is sentinel:
                        break  # server closed the watch (timeout) — reconnect
                    await self._handle_event(event_data)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Event watcher error in namespace %s, retrying in 10s", namespace
                )
                await asyncio.sleep(10)
            finally:
                # Unblock any in-flight stream iteration running in the threadpool.
                w.stop()

    async def _handle_event(self, event_data: dict) -> None:
        """Process a single Kubernetes event."""
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
