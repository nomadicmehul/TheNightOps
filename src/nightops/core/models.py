"""Shared data models for TheNightOps."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


class Severity(str, Enum):
    """Incident severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class IncidentStatus(str, Enum):
    """Incident lifecycle status."""

    TRIGGERED = "triggered"
    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MONITORING = "monitoring"
    RESOLVED = "resolved"


class LogEntry(BaseModel):
    """A structured log entry from Cloud Logging."""

    timestamp: datetime
    severity: str
    message: str
    resource_type: str = ""
    resource_labels: dict[str, str] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)
    json_payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = ""
    span_id: str = ""
    insert_id: str = ""


class ContainerInfo(BaseModel):
    """Container information within a pod."""

    name: str
    image: str
    state: str
    ready: bool = False
    restart_count: int = 0
    last_termination_reason: str = ""
    resource_requests: dict[str, str] = Field(default_factory=dict)
    resource_limits: dict[str, str] = Field(default_factory=dict)


class KubeEvent(BaseModel):
    """A Kubernetes event."""

    timestamp: datetime
    type: str  # Normal, Warning
    reason: str
    message: str
    count: int = 1
    source: str = ""


class PodInfo(BaseModel):
    """Kubernetes pod information."""

    name: str
    namespace: str
    status: str
    node: str = ""
    restart_count: int = 0
    containers: list[ContainerInfo] = Field(default_factory=list)
    events: list[KubeEvent] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    created_at: Optional[datetime] = None


class DeploymentInfo(BaseModel):
    """Kubernetes deployment information."""

    name: str
    namespace: str
    replicas: int
    ready_replicas: int
    updated_replicas: int
    available_replicas: int
    image: str = ""
    last_updated: Optional[datetime] = None
    conditions: list[dict[str, str]] = Field(default_factory=list)


class Incident(BaseModel):
    """An incident tracked by TheNightOps."""

    id: str
    title: str
    description: str = ""
    severity: Severity = Severity.HIGH
    status: IncidentStatus = IncidentStatus.TRIGGERED
    service_name: str = ""
    environment: str = ""
    cluster: str = ""
    namespace: str = ""
    fingerprint: str = ""
    source: str = "manual"  # manual, webhook, event_watcher, anomaly_detector
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    alert_details: dict[str, Any] = Field(default_factory=dict)
    assignee: str = ""

    def compute_fingerprint(self) -> str:
        """Generate a fingerprint for deduplication based on key attributes."""
        key = f"{self.service_name}:{self.namespace}:{self.title}".lower()
        return hashlib.sha256(key.encode()).hexdigest()[:16]


class Investigation(BaseModel):
    """An investigation being conducted by TheNightOps."""

    incident: Incident
    status: IncidentStatus = IncidentStatus.INVESTIGATING
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: Optional[datetime] = None
    findings: list[Finding] = Field(default_factory=list)
    timeline: list[TimelineEntry] = Field(default_factory=list)
    root_cause: str = ""
    root_cause_confidence: float = 0.0
    impact_summary: str = ""
    recommendations: list[str] = Field(default_factory=list)
    remediation_actions: list[RemediationAction] = Field(default_factory=list)
    rca_draft: str = ""
    matched_historical_incidents: list[str] = Field(default_factory=list)
    tools_called: int = 0
    human_interventions: int = 0


class Finding(BaseModel):
    """A finding discovered during investigation."""

    source: str  # which agent/tool found this
    category: str  # log_anomaly, deployment_change, resource_exhaustion, etc.
    description: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.0  # 0.0 to 1.0
    timestamp: Optional[datetime] = None


class TimelineEntry(BaseModel):
    """A timeline entry for the incident."""

    timestamp: datetime
    event: str
    source: str
    details: str = ""


# ── Remediation Models ────────────────────────────────────────────


class RemediationAction(BaseModel):
    """A remediation action that can be taken for an incident."""

    action_type: str  # restart_pod, rollback_deployment, scale_up, revert_configmap, etc.
    description: str
    target: str = ""  # e.g. "deployment/demo-api" or "pod/demo-api-xyz"
    namespace: str = ""
    auto_approved: bool = False
    approved: Optional[bool] = None
    executed_at: Optional[datetime] = None
    result: str = ""
    confidence: float = 0.0


class RemediationPolicy(BaseModel):
    """Policy governing when remediation actions can be auto-approved."""

    action_type: str
    auto_approve_environments: list[str] = Field(default_factory=list)  # e.g. ["dev", "staging"]
    require_approval_environments: list[str] = Field(default_factory=lambda: ["production"])
    conditions: dict[str, Any] = Field(default_factory=dict)  # e.g. {"restart_count_lt": 3}
    blocked: bool = False  # If True, action is never allowed


# ── Webhook / Ingestion Models ────────────────────────────────────


class WebhookAlert(BaseModel):
    """Normalized alert from an external webhook source."""

    source: str  # grafana, alertmanager, pagerduty, custom
    alert_name: str
    status: str = "firing"  # firing, resolved
    severity: Severity = Severity.HIGH
    service: str = ""
    namespace: str = ""
    cluster: str = ""
    environment: str = ""
    description: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    generator_url: str = ""
    fingerprint: str = ""
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    def compute_fingerprint(self) -> str:
        """Generate fingerprint for deduplication."""
        key = f"{self.source}:{self.service}:{self.namespace}:{self.alert_name}".lower()
        return hashlib.sha256(key.encode()).hexdigest()[:16]


class AlertGroup(BaseModel):
    """A group of deduplicated alerts representing a single incident."""

    fingerprint: str
    alerts: list[WebhookAlert] = Field(default_factory=list)
    first_seen: datetime = Field(default_factory=_utcnow)
    last_seen: datetime = Field(default_factory=_utcnow)
    count: int = 1
    incident_id: Optional[str] = None


# ── Intelligence / Memory Models ──────────────────────────────────


class IncidentRecord(BaseModel):
    """Historical incident record stored in the vector store for pattern matching."""

    incident_id: str
    title: str
    service_name: str
    root_cause: str
    resolution: str
    severity: Severity
    environment: str = ""
    cluster: str = ""
    namespace: str = ""
    pattern_type: str = ""  # oom_kill, cpu_spike, cascading_failure, config_drift, etc.
    mttr_seconds: float = 0.0
    findings_summary: str = ""
    action_items: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    resolved_at: Optional[datetime] = None
    embedding_text: str = ""

    def build_embedding_text(self) -> str:
        """Build text for vector embedding from all relevant fields."""
        parts = [
            f"Service: {self.service_name}",
            f"Incident: {self.title}",
            f"Root cause: {self.root_cause}",
            f"Resolution: {self.resolution}",
            f"Pattern: {self.pattern_type}",
            f"Findings: {self.findings_summary}",
        ]
        return " | ".join(parts)


class SimilarIncident(BaseModel):
    """A similar historical incident found via vector search."""

    incident_id: str
    title: str
    root_cause: str
    resolution: str
    similarity_score: float
    mttr_seconds: float = 0.0


# ── Metrics / Impact Models ───────────────────────────────────────


class InvestigationMetrics(BaseModel):
    """Metrics captured for a single investigation."""

    incident_id: str
    time_to_detect_seconds: float = 0.0  # alert fired → agent received
    time_to_identify_seconds: float = 0.0  # agent received → root cause identified
    time_to_resolve_seconds: float = 0.0  # root cause → resolved
    total_mttr_seconds: float = 0.0
    tools_called: int = 0
    human_interventions: int = 0
    confidence_score: float = 0.0
    was_correct: Optional[bool] = None  # filled in by human review
    pattern_matched: bool = False
    auto_remediated: bool = False
    severity: Severity = Severity.MEDIUM
    environment: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


class ImpactSummary(BaseModel):
    """Aggregated impact metrics for the dashboard."""

    total_incidents: int = 0
    avg_mttr_seconds: float = 0.0
    mttr_trend_percent: float = 0.0  # negative = improving
    incidents_auto_resolved: int = 0
    incidents_requiring_human: int = 0
    accuracy_rate: float = 0.0
    estimated_hours_saved: float = 0.0
    top_patterns: list[dict[str, Any]] = Field(default_factory=list)
    period_days: int = 30


# ── Multi-Cluster Models ──────────────────────────────────────────


class ClusterInfo(BaseModel):
    """Information about a Kubernetes cluster in a multi-cluster setup."""

    name: str
    location: str
    project_id: str
    environment: str = "production"  # production, staging, development
    criticality: str = "high"  # high, medium, low
    healthy: bool = True
    last_checked: Optional[datetime] = None


# ── Anomaly Detection Models ──────────────────────────────────────


class AnomalyCheck(BaseModel):
    """Result of a proactive anomaly check."""

    check_name: str
    check_type: str  # pod_health, memory_trending, error_rate, cert_expiry, etc.
    is_anomalous: bool = False
    severity: Severity = Severity.LOW
    description: str = ""
    current_value: str = ""
    threshold: str = ""
    cluster: str = ""
    namespace: str = ""
    service: str = ""
    detected_at: datetime = Field(default_factory=_utcnow)


class RCAReport(BaseModel):
    """A Root Cause Analysis report."""

    incident_id: str
    title: str
    severity: Severity
    timeline: list[TimelineEntry]
    root_cause: str
    impact: str
    contributing_factors: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    lessons_learned: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_utcnow)

    def to_markdown(self) -> str:
        """Render the RCA as a markdown document."""
        lines = [
            f"# RCA: {self.title}",
            "",
            f"**Incident ID:** {self.incident_id}",
            f"**Severity:** {self.severity.value.upper()}",
            f"**Generated:** {self.generated_at.strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "## Timeline",
            "",
        ]
        for entry in self.timeline:
            lines.append(
                f"- **{entry.timestamp.strftime('%H:%M:%S')}** [{entry.source}] {entry.event}"
            )
            if entry.details:
                lines.append(f"  - {entry.details}")

        lines.extend(
            [
                "",
                "## Root Cause",
                "",
                self.root_cause,
                "",
                "## Impact",
                "",
                self.impact,
                "",
            ]
        )

        if self.contributing_factors:
            lines.extend(["## Contributing Factors", ""])
            for factor in self.contributing_factors:
                lines.append(f"- {factor}")
            lines.append("")

        if self.action_items:
            lines.extend(["## Action Items", ""])
            for i, item in enumerate(self.action_items, 1):
                lines.append(f"{i}. {item}")
            lines.append("")

        if self.lessons_learned:
            lines.extend(["## Lessons Learned", ""])
            for lesson in self.lessons_learned:
                lines.append(f"- {lesson}")
            lines.append("")

        return "\n".join(lines)
