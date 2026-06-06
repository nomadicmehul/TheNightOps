"""
Proactive Health Check Scheduler for TheNightOps.

Runs periodic anomaly detection checks and triggers investigations
when problems are detected before external alerts fire.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from nightops.core.config import NightOpsConfig, ProactiveConfig
from nightops.core.models import AnomalyCheck, Incident, Severity
from nightops.ingestion.deduplication import AlertDeduplicator

logger = logging.getLogger(__name__)


class ProactiveScheduler:
    """Schedules and runs periodic health checks via the anomaly detector agent."""

    def __init__(
        self,
        config: ProactiveConfig,
        deduplicator: AlertDeduplicator,
        on_new_incident: Any = None,
    ):
        self.config = config
        self.deduplicator = deduplicator
        self.on_new_incident = on_new_incident
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._checks_run = 0
        self._anomalies_found = 0
        self._last_check: Optional[datetime] = None

    async def start(self) -> None:
        """Start the proactive check scheduler."""
        if not self.config.enabled:
            logger.info("Proactive checks disabled in configuration")
            return

        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info(
            "Proactive scheduler started — interval: %ds, checks: %s",
            self.config.check_interval_seconds,
            self.config.checks,
        )

    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(
            "Proactive scheduler stopped (ran %d checks, found %d anomalies)",
            self._checks_run, self._anomalies_found,
        )

    async def _scheduler_loop(self) -> None:
        """Main scheduler loop."""
        while self._running:
            try:
                await self._run_checks()
                self._last_check = datetime.now(timezone.utc)
                self._checks_run += 1
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in proactive check cycle")

            await asyncio.sleep(self.config.check_interval_seconds)

    async def _run_checks(self) -> list[AnomalyCheck]:
        """Run all configured health checks.

        This method uses the Kubernetes and Prometheus clients directly
        for lightweight checks. For deeper investigation, the anomaly
        detector agent is invoked via ADK.
        """
        results: list[AnomalyCheck] = []

        for check_name in self.config.checks:
            try:
                check_fn = CHECK_REGISTRY.get(check_name)
                if check_fn:
                    check_result = await check_fn(self.config)
                    results.append(check_result)

                    if check_result.is_anomalous:
                        self._anomalies_found += 1
                        await self._handle_anomaly(check_result)
            except Exception:
                logger.exception("Failed to run check: %s", check_name)

        return results

    async def _handle_anomaly(self, check: AnomalyCheck) -> None:
        """Handle a detected anomaly by creating an incident."""
        from nightops.core.models import WebhookAlert
        import hashlib

        fingerprint = hashlib.sha256(
            f"proactive:{check.check_type}:{check.service}:{check.namespace}".encode()
        ).hexdigest()[:16]

        alert = WebhookAlert(
            source="anomaly_detector",
            alert_name=f"Proactive: {check.check_name}",
            severity=check.severity,
            service=check.service,
            namespace=check.namespace,
            cluster=check.cluster,
            description=check.description,
            fingerprint=fingerprint,
        )

        is_new = self.deduplicator.check_and_add(alert)
        if not is_new:
            return

        incident = Incident(
            id=f"inc-{uuid.uuid4().hex[:8]}",
            title=f"PROACTIVE {check.severity.value.upper()}: {check.check_name}",
            description=(
                f"Proactive anomaly detection found an issue.\n\n"
                f"**Check:** {check.check_name}\n"
                f"**Type:** {check.check_type}\n"
                f"**Current Value:** {check.current_value}\n"
                f"**Threshold:** {check.threshold}\n"
                f"**Details:** {check.description}"
            ),
            severity=check.severity,
            service_name=check.service,
            namespace=check.namespace,
            cluster=check.cluster,
            fingerprint=fingerprint,
            source="anomaly_detector",
        )

        logger.info(
            "Proactive detection: %s — creating incident %s",
            check.check_name, incident.id,
        )

        if self.on_new_incident:
            try:
                await self.on_new_incident(incident)
            except Exception:
                logger.exception("Error dispatching proactive incident %s", incident.id)

    @property
    def status(self) -> dict:
        return {
            "running": self._running,
            "checks_run": self._checks_run,
            "anomalies_found": self._anomalies_found,
            "last_check": self._last_check.isoformat() if self._last_check else None,
            "interval_seconds": self.config.check_interval_seconds,
        }


# ── Health Check Functions ────────────────────────────────────────


async def _check_crashloop_pods(config: ProactiveConfig) -> AnomalyCheck:
    """Check for pods in CrashLoopBackOff state."""
    check = AnomalyCheck(
        check_name="CrashLoopBackOff pods",
        check_type="crashloop_pods",
    )

    try:
        from kubernetes import client, config as k8s_config
        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()

        v1 = client.CoreV1Api()
        pods = await asyncio.to_thread(v1.list_pod_for_all_namespaces)

        crashloop_pods = []
        for pod in pods.items:
            if pod.status and pod.status.container_statuses:
                for cs in pod.status.container_statuses:
                    if cs.state and cs.state.waiting and cs.state.waiting.reason == "CrashLoopBackOff":
                        crashloop_pods.append(f"{pod.metadata.namespace}/{pod.metadata.name}")

        if crashloop_pods:
            check.is_anomalous = True
            check.severity = Severity.CRITICAL
            check.description = f"Found {len(crashloop_pods)} pod(s) in CrashLoopBackOff: {', '.join(crashloop_pods[:5])}"
            check.current_value = str(len(crashloop_pods))
            check.threshold = "0"
            if crashloop_pods:
                parts = crashloop_pods[0].split("/")
                check.namespace = parts[0] if len(parts) > 1 else ""
                check.service = parts[-1].rsplit("-", 2)[0]
    except Exception as e:
        logger.debug("CrashLoop check failed (K8s not available): %s", e)

    return check


async def _check_oom_killed(config: ProactiveConfig) -> AnomalyCheck:
    """Check for recently OOMKilled containers."""
    check = AnomalyCheck(
        check_name="OOMKilled containers",
        check_type="oom_killed",
    )

    try:
        from kubernetes import client, config as k8s_config
        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()

        v1 = client.CoreV1Api()
        pods = await asyncio.to_thread(v1.list_pod_for_all_namespaces)

        oom_pods = []
        for pod in pods.items:
            if pod.status and pod.status.container_statuses:
                for cs in pod.status.container_statuses:
                    if cs.last_state and cs.last_state.terminated:
                        if cs.last_state.terminated.reason == "OOMKilled":
                            oom_pods.append(f"{pod.metadata.namespace}/{pod.metadata.name}")

        if oom_pods:
            check.is_anomalous = True
            check.severity = Severity.CRITICAL
            check.description = f"Found {len(oom_pods)} OOMKilled container(s): {', '.join(oom_pods[:5])}"
            check.current_value = str(len(oom_pods))
            check.threshold = "0"
    except Exception as e:
        logger.debug("OOMKilled check failed: %s", e)

    return check


async def _check_memory_trending(config: ProactiveConfig) -> AnomalyCheck:
    """Check for pods approaching memory limits."""
    check = AnomalyCheck(
        check_name="Memory usage trending high",
        check_type="memory_trending",
    )

    try:
        from kubernetes import client, config as k8s_config
        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()

        v1 = client.CoreV1Api()
        pods = await asyncio.to_thread(v1.list_pod_for_all_namespaces)

        # Check pods without memory limits (risky)
        no_limits = []
        for pod in pods.items:
            if pod.spec and pod.spec.containers:
                for container in pod.spec.containers:
                    if not container.resources or not container.resources.limits:
                        no_limits.append(f"{pod.metadata.namespace}/{pod.metadata.name}")
                        break

        if len(no_limits) > 5:
            check.is_anomalous = True
            check.severity = Severity.MEDIUM
            check.description = f"Found {len(no_limits)} pod(s) without memory limits"
            check.current_value = str(len(no_limits))
            check.threshold = "0"
    except Exception as e:
        logger.debug("Memory trending check failed: %s", e)

    return check


async def _check_error_rate_spike(config: ProactiveConfig) -> AnomalyCheck:
    """Check for error rate spikes (placeholder — needs Prometheus)."""
    return AnomalyCheck(
        check_name="Error rate check",
        check_type="error_rate_spike",
        description="Error rate check requires Prometheus connection",
    )


async def _check_deployment_zero_ready(config: ProactiveConfig) -> AnomalyCheck:
    """Check for deployments with zero ready replicas."""
    check = AnomalyCheck(
        check_name="Deployments with zero ready replicas",
        check_type="deployment_zero_ready",
    )

    try:
        from kubernetes import client, config as k8s_config
        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()

        apps_v1 = client.AppsV1Api()
        deployments = await asyncio.to_thread(apps_v1.list_deployment_for_all_namespaces)

        zero_ready = []
        for dep in deployments.items:
            if dep.spec.replicas and dep.spec.replicas > 0:
                ready = dep.status.ready_replicas or 0
                if ready == 0:
                    zero_ready.append(f"{dep.metadata.namespace}/{dep.metadata.name}")

        if zero_ready:
            check.is_anomalous = True
            check.severity = Severity.CRITICAL
            check.description = f"Found {len(zero_ready)} deployment(s) with 0 ready replicas: {', '.join(zero_ready[:5])}"
            check.current_value = str(len(zero_ready))
            check.threshold = "0"
    except Exception as e:
        logger.debug("Deployment zero ready check failed: %s", e)

    return check


# Registry of check functions
CHECK_REGISTRY = {
    "crashloop_pods": _check_crashloop_pods,
    "oom_killed": _check_oom_killed,
    "memory_trending": _check_memory_trending,
    "error_rate_spike": _check_error_rate_spike,
    "deployment_zero_ready": _check_deployment_zero_ready,
}
