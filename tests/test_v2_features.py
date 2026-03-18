"""Tests for v0.2 features: deduplication, incident memory, policy engine, metrics, new models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nightops.core.models import (
    AlertGroup,
    AnomalyCheck,
    ClusterInfo,
    Finding,
    ImpactSummary,
    Incident,
    IncidentRecord,
    IncidentStatus,
    Investigation,
    InvestigationMetrics,
    RemediationAction,
    RemediationPolicy,
    Severity,
    SimilarIncident,
    WebhookAlert,
)


# ── New Model Tests ──────────────────────────────────────────────


class TestWebhookAlert:
    def test_creation(self):
        alert = WebhookAlert(
            source="grafana",
            alert_name="HighErrorRate",
            severity=Severity.HIGH,
            service="demo-api",
            namespace="default",
        )
        assert alert.source == "grafana"
        assert alert.status == "firing"
        assert alert.severity == Severity.HIGH

    def test_compute_fingerprint(self):
        alert = WebhookAlert(
            source="grafana",
            alert_name="HighErrorRate",
            service="demo-api",
            namespace="default",
        )
        fp = alert.compute_fingerprint()
        assert isinstance(fp, str)
        assert len(fp) == 16

    def test_same_alert_same_fingerprint(self):
        a1 = WebhookAlert(source="grafana", alert_name="HighErrorRate", service="demo-api", namespace="default")
        a2 = WebhookAlert(source="grafana", alert_name="HighErrorRate", service="demo-api", namespace="default")
        assert a1.compute_fingerprint() == a2.compute_fingerprint()

    def test_different_alert_different_fingerprint(self):
        a1 = WebhookAlert(source="grafana", alert_name="HighErrorRate", service="demo-api", namespace="default")
        a2 = WebhookAlert(source="grafana", alert_name="LowMemory", service="demo-api", namespace="default")
        assert a1.compute_fingerprint() != a2.compute_fingerprint()


class TestIncidentFingerprint:
    def test_incident_compute_fingerprint(self):
        incident = Incident(id="INC-001", title="demo-api OOMKilled", service_name="demo-api", namespace="default")
        fp = incident.compute_fingerprint()
        assert isinstance(fp, str)
        assert len(fp) == 16


class TestRemediationAction:
    def test_creation(self):
        action = RemediationAction(
            action_type="restart_pod",
            description="Restart the pod",
            target="pod/demo-api-xyz",
            namespace="default",
            confidence=0.8,
        )
        assert action.action_type == "restart_pod"
        assert action.auto_approved is False
        assert action.approved is None
        assert action.confidence == 0.8


class TestRemediationPolicy:
    def test_creation(self):
        policy = RemediationPolicy(
            action_type="restart_pod",
            auto_approve_environments=["development", "staging"],
            require_approval_environments=["production"],
        )
        assert policy.action_type == "restart_pod"
        assert "development" in policy.auto_approve_environments
        assert not policy.blocked


class TestIncidentRecord:
    def test_creation_and_embedding(self):
        record = IncidentRecord(
            incident_id="INC-001",
            title="OOMKilled in demo-api",
            service_name="demo-api",
            root_cause="Memory leak in v2 image",
            resolution="Rolled back to v1",
            severity=Severity.HIGH,
            pattern_type="oom_kill",
        )
        text = record.build_embedding_text()
        assert "demo-api" in text
        assert "Memory leak" in text
        assert "oom_kill" in text


class TestAnomalyCheck:
    def test_creation(self):
        check = AnomalyCheck(
            check_name="crashloop_pods",
            check_type="pod_health",
            is_anomalous=True,
            severity=Severity.HIGH,
            description="3 pods in CrashLoopBackOff",
        )
        assert check.is_anomalous is True
        assert check.severity == Severity.HIGH


class TestClusterInfo:
    def test_creation(self):
        cluster = ClusterInfo(
            name="prod-us-central1",
            location="us-central1",
            project_id="my-project",
            environment="production",
        )
        assert cluster.healthy is True
        assert cluster.criticality == "high"


class TestInvestigationMetrics:
    def test_creation(self):
        metrics = InvestigationMetrics(
            incident_id="INC-001",
            total_mttr_seconds=120.0,
            tools_called=5,
            confidence_score=0.9,
            severity=Severity.HIGH,
        )
        assert metrics.total_mttr_seconds == 120.0
        assert metrics.was_correct is None


class TestImpactSummary:
    def test_empty_summary(self):
        summary = ImpactSummary()
        assert summary.total_incidents == 0
        assert summary.avg_mttr_seconds == 0.0
        assert summary.estimated_hours_saved == 0.0


class TestAlertGroup:
    def test_creation(self):
        group = AlertGroup(fingerprint="abc123", count=5)
        assert group.count == 5
        assert group.incident_id is None


# ── Deduplication Tests ──────────────────────────────────────────


class TestAlertDeduplicator:
    def test_new_alert_returns_true(self):
        from nightops.ingestion.deduplication import AlertDeduplicator

        dedup = AlertDeduplicator(window_seconds=300)
        alert = WebhookAlert(
            source="grafana",
            alert_name="HighErrorRate",
            service="demo-api",
            namespace="default",
        )
        assert dedup.check_and_add(alert) is True

    def test_duplicate_alert_returns_false(self):
        from nightops.ingestion.deduplication import AlertDeduplicator

        dedup = AlertDeduplicator(window_seconds=300)
        alert = WebhookAlert(
            source="grafana",
            alert_name="HighErrorRate",
            service="demo-api",
            namespace="default",
        )
        assert dedup.check_and_add(alert) is True
        assert dedup.check_and_add(alert) is False

    def test_different_alerts_both_new(self):
        from nightops.ingestion.deduplication import AlertDeduplicator

        dedup = AlertDeduplicator(window_seconds=300)
        a1 = WebhookAlert(source="grafana", alert_name="HighErrorRate", service="demo-api", namespace="default")
        a2 = WebhookAlert(source="grafana", alert_name="LowMemory", service="demo-api", namespace="default")
        assert dedup.check_and_add(a1) is True
        assert dedup.check_and_add(a2) is True

    def test_resolve_removes_group(self):
        from nightops.ingestion.deduplication import AlertDeduplicator

        dedup = AlertDeduplicator(window_seconds=300)
        alert = WebhookAlert(source="grafana", alert_name="HighErrorRate", service="demo-api", namespace="default")
        dedup.check_and_add(alert)

        group = dedup.resolve(alert)
        assert group is not None
        assert group.count == 1

        # After resolve, same alert should be new again
        assert dedup.check_and_add(alert) is True

    def test_get_active_groups(self):
        from nightops.ingestion.deduplication import AlertDeduplicator

        dedup = AlertDeduplicator(window_seconds=300)
        a1 = WebhookAlert(source="grafana", alert_name="HighErrorRate", service="demo-api", namespace="default")
        a2 = WebhookAlert(source="grafana", alert_name="LowMemory", service="demo-api", namespace="default")
        dedup.check_and_add(a1)
        dedup.check_and_add(a2)
        assert len(dedup.get_active_groups()) == 2

    def test_duplicate_increments_count(self):
        from nightops.ingestion.deduplication import AlertDeduplicator

        dedup = AlertDeduplicator(window_seconds=300)
        alert = WebhookAlert(source="grafana", alert_name="HighErrorRate", service="demo-api", namespace="default")
        dedup.check_and_add(alert)
        dedup.check_and_add(alert)
        dedup.check_and_add(alert)

        fp = alert.compute_fingerprint()
        group = dedup.get_group(fp)
        assert group is not None
        assert group.count == 3


# ── Incident Memory Tests ────────────────────────────────────────


class TestIncidentMemory:
    def _make_investigation(self, incident_id: str, title: str, service: str, root_cause: str) -> Investigation:
        now = datetime.now(timezone.utc)
        return Investigation(
            incident=Incident(
                id=incident_id,
                title=title,
                service_name=service,
                severity=Severity.HIGH,
            ),
            started_at=now - timedelta(minutes=10),
            completed_at=now,
            root_cause=root_cause,
            root_cause_confidence=0.9,
            findings=[
                Finding(
                    source="log_analyst",
                    category="log_anomaly",
                    description=root_cause,
                    confidence=0.9,
                )
            ],
            recommendations=["Fix the issue"],
        )

    def test_record_and_find_similar(self, tmp_path):
        from nightops.core.config import IntelligenceConfig
        from nightops.intelligence.incident_memory import IncidentMemory

        config = IntelligenceConfig(store_path=str(tmp_path / "incidents"), similarity_threshold=0.1)
        memory = IncidentMemory(config)

        # Record two investigations
        inv1 = self._make_investigation("INC-001", "demo-api OOMKilled", "demo-api", "Memory leak in v2 image")
        inv2 = self._make_investigation("INC-002", "payment-svc CPU spike", "payment-svc", "Infinite loop in handler")
        memory.record_investigation(inv1)
        memory.record_investigation(inv2)
        assert memory.total_records == 2

        # Search for similar (OOM-related query)
        results = memory.find_similar("OOMKilled pods in demo-api service", service_name="demo-api")
        assert len(results) > 0
        assert results[0].incident_id == "INC-001"

    def test_pattern_stats(self, tmp_path):
        from nightops.core.config import IntelligenceConfig
        from nightops.intelligence.incident_memory import IncidentMemory

        config = IntelligenceConfig(store_path=str(tmp_path / "incidents"), similarity_threshold=0.1)
        memory = IncidentMemory(config)

        inv = self._make_investigation("INC-001", "demo-api OOMKilled", "demo-api", "OOMKilled container memory limit exceeded")
        memory.record_investigation(inv)

        stats = memory.get_pattern_stats()
        assert "oom_kill" in stats

    def test_persistence(self, tmp_path):
        from nightops.core.config import IntelligenceConfig
        from nightops.intelligence.incident_memory import IncidentMemory

        config = IntelligenceConfig(store_path=str(tmp_path / "incidents"))
        memory = IncidentMemory(config)

        inv = self._make_investigation("INC-001", "demo-api OOMKilled", "demo-api", "Memory leak")
        memory.record_investigation(inv)

        # Create a new instance — should load from disk
        memory2 = IncidentMemory(config)
        assert memory2.total_records == 1

    def test_empty_find_similar(self, tmp_path):
        from nightops.core.config import IntelligenceConfig
        from nightops.intelligence.incident_memory import IncidentMemory

        config = IntelligenceConfig(store_path=str(tmp_path / "incidents"))
        memory = IncidentMemory(config)
        results = memory.find_similar("anything")
        assert results == []


# ── Policy Engine Tests ──────────────────────────────────────────


class TestPolicyEngine:
    def test_auto_approve_level0(self):
        from nightops.remediation.policy_engine import PolicyEngine

        engine = PolicyEngine()
        action = RemediationAction(action_type="silence_alert", description="Silence alert")
        result = engine.evaluate(action, environment="production")
        assert result.auto_approved is True
        assert result.approved is True

    def test_environment_gated_dev(self):
        from nightops.remediation.policy_engine import PolicyEngine

        engine = PolicyEngine()
        action = RemediationAction(action_type="restart_pod", description="Restart pod")
        result = engine.evaluate(action, environment="development")
        assert result.auto_approved is True

    def test_environment_gated_prod(self):
        from nightops.remediation.policy_engine import PolicyEngine

        engine = PolicyEngine()
        action = RemediationAction(action_type="restart_pod", description="Restart pod")
        result = engine.evaluate(action, environment="production")
        assert result.auto_approved is False
        assert result.approved is None  # Pending human approval

    def test_always_require_approval(self):
        from nightops.remediation.policy_engine import PolicyEngine

        engine = PolicyEngine()
        action = RemediationAction(action_type="rollback_deployment", description="Rollback")
        result = engine.evaluate(action, environment="development")
        assert result.auto_approved is False
        assert result.approved is None

    def test_blocked_action(self):
        from nightops.remediation.policy_engine import PolicyEngine

        engine = PolicyEngine()
        action = RemediationAction(action_type="delete_namespace", description="Delete ns")
        result = engine.evaluate(action, environment="development")
        assert result.auto_approved is False
        assert result.approved is False
        assert "BLOCKED" in result.result

    def test_get_suggested_remediations_oom(self):
        from nightops.remediation.policy_engine import PolicyEngine

        engine = PolicyEngine()
        actions = engine.get_suggested_remediations("oom_kill", environment="staging")
        assert len(actions) >= 1
        action_types = [a.action_type for a in actions]
        assert "restart_pod" in action_types

    def test_policy_summary(self):
        from nightops.remediation.policy_engine import PolicyEngine

        engine = PolicyEngine()
        summary = engine.get_policy_summary()
        assert "silence_alert" in summary
        assert "delete_namespace" in summary
        assert summary["delete_namespace"] == "BLOCKED"

    def test_load_from_yaml(self, tmp_path):
        from nightops.remediation.policy_engine import PolicyEngine

        policy_yaml = tmp_path / "policies.yaml"
        policy_yaml.write_text("""
policies:
  custom_action:
    auto_approve_environments: ["*"]
    blocked: false
""")
        engine = PolicyEngine(policy_path=str(policy_yaml))
        action = RemediationAction(action_type="custom_action", description="Custom")
        result = engine.evaluate(action, environment="production")
        assert result.auto_approved is True

    def test_unknown_action_requires_approval(self):
        from nightops.remediation.policy_engine import PolicyEngine

        engine = PolicyEngine()
        action = RemediationAction(action_type="unknown_action", description="Unknown")
        result = engine.evaluate(action, environment="production")
        # Unknown actions have no policy, so they fall through to require approval
        assert result.auto_approved is False


# ── Metrics Tracker Tests ────────────────────────────────────────


class TestMetricsTracker:
    def _make_investigation(self) -> Investigation:
        now = datetime.now(timezone.utc)
        return Investigation(
            incident=Incident(
                id="INC-001",
                title="demo-api OOMKilled",
                service_name="demo-api",
                severity=Severity.HIGH,
                created_at=now - timedelta(minutes=12),
            ),
            started_at=now - timedelta(minutes=10),
            completed_at=now,
            root_cause="Memory leak",
            root_cause_confidence=0.85,
            tools_called=8,
            human_interventions=1,
        )

    def test_record_investigation(self, tmp_path):
        from nightops.core.config import MetricsConfig
        from nightops.metrics.tracker import MetricsTracker

        config = MetricsConfig(store_path=str(tmp_path / "metrics"))
        tracker = MetricsTracker(config)
        inv = self._make_investigation()
        metrics = tracker.record_investigation(inv)

        assert metrics.incident_id == "INC-001"
        assert metrics.total_mttr_seconds > 0
        assert metrics.tools_called == 8
        assert metrics.confidence_score == 0.85

    def test_impact_summary(self, tmp_path):
        from nightops.core.config import MetricsConfig
        from nightops.metrics.tracker import MetricsTracker

        config = MetricsConfig(store_path=str(tmp_path / "metrics"), estimated_manual_mttr_seconds=2700.0)
        tracker = MetricsTracker(config)

        # Record 3 investigations
        for i in range(3):
            inv = self._make_investigation()
            inv.incident.id = f"INC-{i:03d}"
            tracker.record_investigation(inv)

        summary = tracker.get_impact_summary(period_days=30)
        assert summary.total_incidents == 3
        assert summary.avg_mttr_seconds > 0
        assert summary.estimated_hours_saved > 0

    def test_empty_impact_summary(self, tmp_path):
        from nightops.core.config import MetricsConfig
        from nightops.metrics.tracker import MetricsTracker

        config = MetricsConfig(store_path=str(tmp_path / "metrics"))
        tracker = MetricsTracker(config)
        summary = tracker.get_impact_summary()
        assert summary.total_incidents == 0

    def test_mark_correct(self, tmp_path):
        from nightops.core.config import MetricsConfig
        from nightops.metrics.tracker import MetricsTracker

        config = MetricsConfig(store_path=str(tmp_path / "metrics"))
        tracker = MetricsTracker(config)
        inv = self._make_investigation()
        tracker.record_investigation(inv)

        assert tracker.mark_correct("INC-001", True) is True
        assert tracker.mark_correct("INC-999", True) is False

    def test_persistence(self, tmp_path):
        from nightops.core.config import MetricsConfig
        from nightops.metrics.tracker import MetricsTracker

        config = MetricsConfig(store_path=str(tmp_path / "metrics"))
        tracker = MetricsTracker(config)
        inv = self._make_investigation()
        tracker.record_investigation(inv)

        # New instance should load from disk
        tracker2 = MetricsTracker(config)
        assert tracker2.total_records == 1


# ── Anomaly Detector Agent Tests ─────────────────────────────────


class TestAnomalyDetectorAgent:
    def test_agent_creation(self):
        from nightops.agents.anomaly_detector import create_anomaly_detector_agent

        agent = create_anomaly_detector_agent()
        assert agent.name == "anomaly_detector"
        assert "proactive" in agent.description.lower() or "anomal" in agent.description.lower()

    def test_agent_custom_model(self):
        from nightops.agents.anomaly_detector import create_anomaly_detector_agent

        agent = create_anomaly_detector_agent(model="gemini-3-flash")
        assert agent.model == "gemini-3-flash"


# ── Config v0.2 Tests ────────────────────────────────────────────


class TestConfigV2:
    def test_supported_models(self):
        from nightops.core.config import SUPPORTED_MODELS

        assert "gemini-3.1-pro" in SUPPORTED_MODELS
        assert "gemini-3-flash" in SUPPORTED_MODELS
        assert "gemini-2.5-flash" in SUPPORTED_MODELS

    def test_webhook_config_defaults(self):
        from nightops.core.config import WebhookConfig

        config = WebhookConfig()
        assert config.port == 8090
        assert config.dedup_window_seconds == 300
        assert config.enabled is True

    def test_event_watcher_config_defaults(self):
        from nightops.core.config import EventWatcherConfig

        config = EventWatcherConfig()
        assert "OOMKilled" in config.watch_reasons
        assert "CrashLoopBackOff" in config.watch_reasons

    def test_intelligence_config_defaults(self):
        from nightops.core.config import IntelligenceConfig

        config = IntelligenceConfig()
        assert config.similarity_threshold == 0.3

    def test_remediation_config_defaults(self):
        from nightops.core.config import RemediationConfig

        config = RemediationConfig()
        assert config.default_require_approval is True

    def test_proactive_config_defaults(self):
        from nightops.core.config import ProactiveConfig

        config = ProactiveConfig()
        assert "crashloop_pods" in config.checks
        assert config.memory_threshold_percent == 85.0

    def test_metrics_config_defaults(self):
        from nightops.core.config import MetricsConfig

        config = MetricsConfig()
        assert config.estimated_manual_mttr_seconds == 2700.0

    def test_nightops_config_has_new_sections(self):
        from nightops.core.config import NightOpsConfig

        config = NightOpsConfig()
        assert config.webhook is not None
        assert config.event_watcher is not None
        assert config.intelligence is not None
        assert config.remediation is not None
        assert config.proactive is not None
        assert config.metrics is not None
        assert isinstance(config.clusters, list)
