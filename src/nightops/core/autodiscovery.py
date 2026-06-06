"""
Auto-Discovery for TheNightOps.

Automatically detects available infrastructure (GCP project, GKE clusters,
Grafana instances, notification channels) and generates configuration.

Usage:
    nightops auto-config  →  Probes environment and auto-configures
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredGCP:
    """Discovered GCP project information."""

    project_id: str = ""
    authenticated: bool = False
    apis_enabled: list[str] = field(default_factory=list)


@dataclass
class DiscoveredCluster:
    """A discovered Kubernetes cluster."""

    name: str = ""
    location: str = ""
    project_id: str = ""
    status: str = ""
    is_current_context: bool = False


@dataclass
class DiscoveredGrafana:
    """A discovered Grafana instance."""

    url: str = ""
    reachable: bool = False
    has_token: bool = False


@dataclass
class DiscoveredNotifications:
    """Discovered notification channel availability."""

    slack: bool = False
    email: bool = False
    telegram: bool = False
    whatsapp: bool = False


@dataclass
class DiscoveredEnvironment:
    """Complete environment discovery result."""

    gcp: DiscoveredGCP = field(default_factory=DiscoveredGCP)
    clusters: list[DiscoveredCluster] = field(default_factory=list)
    grafana: DiscoveredGrafana = field(default_factory=DiscoveredGrafana)
    notifications: DiscoveredNotifications = field(default_factory=DiscoveredNotifications)
    kubeconfig_available: bool = False
    in_cluster: bool = False


class EnvironmentDiscovery:
    """Automatically detect available infrastructure."""

    async def discover(self) -> DiscoveredEnvironment:
        """Run all discovery probes concurrently."""
        results = await asyncio.gather(
            self._detect_gcp(),
            self._detect_clusters(),
            self._detect_grafana(),
            self._detect_notifications(),
            self._detect_kubernetes_context(),
            return_exceptions=True,
        )

        env = DiscoveredEnvironment()

        if isinstance(results[0], DiscoveredGCP):
            env.gcp = results[0]
        if isinstance(results[1], list):
            env.clusters = results[1]
        if isinstance(results[2], DiscoveredGrafana):
            env.grafana = results[2]
        if isinstance(results[3], DiscoveredNotifications):
            env.notifications = results[3]
        if isinstance(results[4], dict):
            env.kubeconfig_available = results[4].get("kubeconfig", False)
            env.in_cluster = results[4].get("in_cluster", False)

        return env

    async def _detect_gcp(self) -> DiscoveredGCP:
        """Detect GCP project and authentication."""
        gcp = DiscoveredGCP()

        # Check GOOGLE_APPLICATION_CREDENTIALS
        creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        if creds_path and os.path.exists(creds_path):
            gcp.authenticated = True

        # Check GCP_PROJECT_ID env var
        gcp.project_id = os.getenv("GCP_PROJECT_ID", "")

        # Try gcloud config
        if not gcp.project_id:
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["gcloud", "config", "get-value", "project"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    gcp.project_id = result.stdout.strip()
                    gcp.authenticated = True
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        # Check metadata server (running on GCE/GKE)
        if not gcp.project_id:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=2) as client:
                    resp = await client.get(
                        "http://metadata.google.internal/computeMetadata/v1/project/project-id",
                        headers={"Metadata-Flavor": "Google"},
                    )
                    if resp.status_code == 200:
                        gcp.project_id = resp.text.strip()
                        gcp.authenticated = True
            except Exception:
                pass

        # Check which APIs are enabled
        if gcp.project_id and gcp.authenticated:
            for api in ["logging.googleapis.com", "container.googleapis.com", "monitoring.googleapis.com"]:
                try:
                    result = await asyncio.to_thread(
                        subprocess.run,
                        ["gcloud", "services", "list", "--enabled", f"--filter=name:{api}",
                         "--format=value(name)", f"--project={gcp.project_id}"],
                        capture_output=True, text=True, timeout=10,
                    )
                    if api in result.stdout:
                        gcp.apis_enabled.append(api)
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    break

        return gcp

    async def _detect_clusters(self) -> list[DiscoveredCluster]:
        """Detect available GKE clusters."""
        clusters = []

        project_id = os.getenv("GCP_PROJECT_ID", "")
        if not project_id:
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["gcloud", "config", "get-value", "project"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    project_id = result.stdout.strip()
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return clusters

        if not project_id:
            return clusters

        # List GKE clusters
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["gcloud", "container", "clusters", "list",
                 f"--project={project_id}", "--format=json"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                import json
                cluster_data = json.loads(result.stdout)
                for c in cluster_data:
                    clusters.append(DiscoveredCluster(
                        name=c.get("name", ""),
                        location=c.get("location", c.get("zone", "")),
                        project_id=project_id,
                        status=c.get("status", ""),
                    ))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Check current kubectl context
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["kubectl", "config", "current-context"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                context = result.stdout.strip()
                for cluster in clusters:
                    if cluster.name in context:
                        cluster.is_current_context = True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return clusters

    async def _detect_grafana(self) -> DiscoveredGrafana:
        """Detect Grafana availability."""
        grafana = DiscoveredGrafana()

        # Check env vars
        grafana.url = os.getenv("GRAFANA_URL", "")
        grafana.has_token = bool(os.getenv("GRAFANA_SERVICE_ACCOUNT_TOKEN", ""))

        # Probe common URLs
        urls_to_try = [grafana.url] if grafana.url else []
        urls_to_try.extend([
            "http://localhost:3000",
            "http://grafana:3000",
        ])

        for url in urls_to_try:
            if not url:
                continue
            try:
                import httpx
                async with httpx.AsyncClient(timeout=3) as client:
                    resp = await client.get(f"{url}/api/health")
                    if resp.status_code == 200:
                        grafana.url = url
                        grafana.reachable = True
                        break
            except Exception:
                continue

        return grafana

    async def _detect_notifications(self) -> DiscoveredNotifications:
        """Detect available notification channels from environment."""
        return DiscoveredNotifications(
            slack=bool(os.getenv("SLACK_BOT_TOKEN", "")),
            email=bool(os.getenv("SMTP_HOST", "")),
            telegram=bool(os.getenv("TELEGRAM_BOT_TOKEN", "")),
            whatsapp=bool(os.getenv("WHATSAPP_API_TOKEN", "")),
        )

    async def _detect_kubernetes_context(self) -> dict:
        """Detect if kubeconfig is available or running in-cluster."""
        result = {"kubeconfig": False, "in_cluster": False}

        # Check in-cluster
        if os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token"):
            result["in_cluster"] = True
            result["kubeconfig"] = True
            return result

        # Check kubeconfig
        try:
            res = await asyncio.to_thread(
                subprocess.run,
                ["kubectl", "cluster-info"],
                capture_output=True, text=True, timeout=5,
            )
            result["kubeconfig"] = res.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return result


def generate_config_from_discovery(env: DiscoveredEnvironment) -> dict:
    """Generate a NightOps config dict from discovered environment."""
    config = {
        "agent": {"model": "gemini-2.5-flash"},
        "mcp_servers": {},
    }

    # GCP / Cloud Observability
    if env.gcp.project_id:
        config["mcp_servers"]["cloud_observability"] = {
            "type": "official",
            "project_id": env.gcp.project_id,
            "enabled": "logging.googleapis.com" in env.gcp.apis_enabled,
        }

    # GKE
    if env.clusters:
        primary = next((c for c in env.clusters if c.is_current_context), env.clusters[0])
        config["mcp_servers"]["gke"] = {
            "type": "official",
            "project_id": primary.project_id,
            "cluster": primary.name,
            "location": primary.location,
            "enabled": True,
        }

        # Multi-cluster
        if len(env.clusters) > 1:
            config["clusters"] = [
                {
                    "name": c.name,
                    "project_id": c.project_id,
                    "location": c.location,
                    "environment": _guess_environment(c.name),
                    "enabled": True,
                }
                for c in env.clusters
            ]

    # Grafana
    if env.grafana.reachable:
        config["mcp_servers"]["grafana"] = {
            "type": "official",
            "url": env.grafana.url,
            "enabled": True,
        }

    # Notifications
    if env.notifications.slack:
        config["mcp_servers"]["slack"] = {"type": "custom", "enabled": True}
    if env.notifications.email or env.notifications.telegram or env.notifications.whatsapp:
        config["mcp_servers"]["notifications"] = {"type": "custom", "enabled": True}

    return config


def _guess_environment(cluster_name: str) -> str:
    """Guess environment from cluster name."""
    name = cluster_name.lower()
    if any(k in name for k in ["prod", "prd"]):
        return "production"
    if any(k in name for k in ["staging", "stg", "stage"]):
        return "staging"
    if any(k in name for k in ["dev", "development"]):
        return "development"
    return "production"
