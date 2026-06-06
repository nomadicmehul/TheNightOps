"""Tests for the outcome recorder (the learning-loop write-back)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from nightops.core.config import NightOpsConfig
from nightops.core.models import Incident, Severity
from nightops.intelligence.incident_memory import IncidentMemory, detect_pattern_type
from nightops.intelligence.recorder import (
    finalize_investigation,
    parse_confidence,
    parse_root_cause,
)
from nightops.metrics.tracker import MetricsTracker

SAMPLE_RCA = """**Incident Summary**: checkout pod killed
**Severity**: critical
**Root Cause**: The checkout-api container was OOMKilled because its memory limit was too low.
**Evidence**: kube events show OOMKilled; logs show heap growth.
**Confidence Level**: high
"""


@pytest.fixture
def tmp_config(tmp_path):
    config = NightOpsConfig()
    config.intelligence.store_path = str(tmp_path / "mem")
    config.intelligence.similarity_threshold = 0.01  # exercise retrieval, not TF-IDF tuning
    config.metrics.store_path = str(tmp_path / "metrics")
    return config


def test_parse_root_cause_extracts_line():
    rc = parse_root_cause(SAMPLE_RCA)
    assert "OOMKilled" in rc
    assert "Incident Summary" not in rc  # only the root-cause line


def test_parse_root_cause_falls_back_to_full_text():
    rc = parse_root_cause("no structured headers here, just prose about a crash")
    assert "crash" in rc


def test_parse_confidence_maps_levels():
    assert parse_confidence(SAMPLE_RCA) == 0.9
    assert parse_confidence("**Confidence Level**: low") == 0.3
    assert parse_confidence("no confidence stated") == 0.5


def test_detect_pattern_type_oom():
    assert detect_pattern_type(SAMPLE_RCA) == "oom_kill"


def test_finalize_records_to_memory_and_metrics(tmp_config):
    started = datetime.now(UTC) - timedelta(seconds=90)
    incident = Incident(
        id="inc-test01",
        title="checkout OOM",
        severity=Severity.CRITICAL,
        service_name="checkout-api",
        environment="production",
        namespace="payments",
        source="event_watcher",
    )

    section = finalize_investigation(
        tmp_config,
        incident=incident,
        incident_description="checkout pod OOMKilled",
        incident_id="inc-test01",
        started_at=started,
        result_text=SAMPLE_RCA,
        tools_called=4,
    )

    # Memory write-back happened and is retrievable (loop is closed)
    memory = IncidentMemory(tmp_config.intelligence)
    assert memory.total_records == 1
    similar = memory.find_similar("checkout container ran out of memory")
    assert similar and similar[0].incident_id == "inc-test01"

    # Metrics write-back happened with a real (non-zero) MTTR
    tracker = MetricsTracker(tmp_config.metrics)
    assert tracker.total_records == 1
    summary = tracker.get_impact_summary(period_days=1)
    assert summary.total_incidents == 1
    assert summary.avg_mttr_seconds > 0

    # Advisory remediation surfaced and policy-classified for prod OOM
    assert "Suggested Remediations" in section
    assert "rollback_deployment" in section
    assert "NEEDS APPROVAL" in section  # rollback always needs approval


def test_finalize_auto_approves_in_dev(tmp_config):
    incident = Incident(
        id="inc-dev01",
        title="dev OOM",
        environment="development",
        source="manual",
    )
    section = finalize_investigation(
        tmp_config,
        incident=incident,
        incident_description="OOMKilled in dev",
        incident_id="inc-dev01",
        started_at=datetime.now(UTC),
        result_text=SAMPLE_RCA,
        tools_called=2,
    )
    # restart_pod is auto-approved in development
    assert "AUTO-APPROVED" in section


def test_finalize_is_fail_safe_when_disabled(tmp_config):
    tmp_config.intelligence.enabled = False
    tmp_config.metrics.enabled = False
    tmp_config.remediation.enabled = False
    section = finalize_investigation(
        tmp_config,
        incident=None,
        incident_description="some incident",
        incident_id=None,
        started_at=datetime.now(UTC),
        result_text=SAMPLE_RCA,
        tools_called=1,
    )
    # No remediation section when remediation disabled; nothing raised.
    assert section == ""
