"""Tests for data models."""

from __future__ import annotations

from datetime import datetime

from nightops.core.models import (
    Finding,
    Incident,
    IncidentStatus,
    RCAReport,
    Severity,
    TimelineEntry,
)


def test_incident_creation():
    """Test creating an incident model."""
    incident = Incident(
        id="INC-001",
        title="demo-api pods OOMKilled",
        severity=Severity.HIGH,
        service_name="demo-api",
    )
    assert incident.id == "INC-001"
    assert incident.severity == Severity.HIGH
    assert incident.status == IncidentStatus.TRIGGERED


def test_finding_creation():
    """Test creating a finding."""
    finding = Finding(
        source="log_analyst",
        category="log_anomaly",
        description="Error rate spiked 5x in the last 30 minutes",
        confidence=0.85,
        evidence=["100 errors in 30 min vs baseline of 20"],
    )
    assert finding.confidence == 0.85
    assert len(finding.evidence) == 1


def test_rca_report_to_markdown():
    """Test RCA report markdown rendering."""
    rca = RCAReport(
        incident_id="INC-001",
        title="Memory Leak in demo-api",
        severity=Severity.HIGH,
        timeline=[
            TimelineEntry(
                timestamp=datetime(2024, 1, 15, 2, 30, 0),
                event="Deployment updated to v2-leaky",
                source="deployment_correlator",
            ),
            TimelineEntry(
                timestamp=datetime(2024, 1, 15, 2, 45, 0),
                event="First OOMKill detected",
                source="log_analyst",
            ),
        ],
        root_cause="Memory leak in v2-leaky image causing OOMKill",
        impact="Service degraded for 15 minutes, affecting ~500 users",
        action_items=[
            "Rollback to v1 immediately",
            "Fix memory leak in v2 before redeploying",
        ],
        lessons_learned=[
            "Add memory usage monitoring alerts",
            "Implement canary deployments",
        ],
    )

    md = rca.to_markdown()
    assert "# RCA: Memory Leak in demo-api" in md
    assert "INC-001" in md
    assert "OOMKill" in md
    assert "Rollback to v1" in md
    assert "canary deployments" in md


def test_severity_values():
    """Test severity enum values."""
    assert Severity.CRITICAL.value == "critical"
    assert Severity.HIGH.value == "high"
    assert Severity.MEDIUM.value == "medium"
    assert Severity.LOW.value == "low"
