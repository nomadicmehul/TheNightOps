"""
Impact Metrics Tracker for TheNightOps.

Tracks and aggregates metrics that demonstrate the agent's value:
- MTTR over time
- Incidents auto-resolved vs requiring human intervention
- Accuracy rate of root cause identification
- Estimated engineer hours saved
- Top recurring incident patterns
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from nightops.core.config import MetricsConfig
from nightops.core.models import (
    ImpactSummary,
    Investigation,
    InvestigationMetrics,
    Severity,
)

logger = logging.getLogger(__name__)


class MetricsTracker:
    """Tracks investigation metrics and computes impact summaries."""

    def __init__(self, config: MetricsConfig):
        self.config = config
        self.store_path = Path(config.store_path)
        self.store_path.mkdir(parents=True, exist_ok=True)
        self._metrics_file = self.store_path / "investigation_metrics.json"
        self._records: list[InvestigationMetrics] = []
        self._load()

    def _load(self) -> None:
        """Load metrics from disk."""
        if self._metrics_file.exists():
            try:
                data = json.loads(self._metrics_file.read_text())
                self._records = [InvestigationMetrics(**r) for r in data]
                logger.info("Loaded %d metric records", len(self._records))
            except Exception:
                logger.exception("Failed to load metrics, starting fresh")
                self._records = []

    def _save(self) -> None:
        """Persist metrics to disk."""
        data = [r.model_dump(mode="json") for r in self._records]
        self._metrics_file.write_text(json.dumps(data, indent=2, default=str))

    def record_investigation(self, investigation: Investigation) -> InvestigationMetrics:
        """Record metrics for a completed investigation."""
        incident = investigation.incident

        # Calculate timing
        started = investigation.started_at
        completed = investigation.completed_at or datetime.now(timezone.utc)

        time_to_detect = 0.0
        if incident.created_at and started:
            time_to_detect = max(0, (started - incident.created_at).total_seconds())

        total_mttr = (completed - started).total_seconds()

        # Time to identify = when root cause was found (estimate from confidence)
        time_to_identify = total_mttr * 0.6 if investigation.root_cause else total_mttr

        metrics = InvestigationMetrics(
            incident_id=incident.id,
            time_to_detect_seconds=time_to_detect,
            time_to_identify_seconds=time_to_identify,
            time_to_resolve_seconds=total_mttr - time_to_identify,
            total_mttr_seconds=total_mttr,
            tools_called=investigation.tools_called,
            human_interventions=investigation.human_interventions,
            confidence_score=investigation.root_cause_confidence,
            pattern_matched=len(investigation.matched_historical_incidents) > 0,
            auto_remediated=any(a.auto_approved for a in investigation.remediation_actions),
            severity=incident.severity,
            environment=incident.environment,
        )

        self._records.append(metrics)
        self._save()

        logger.info(
            "Recorded metrics for %s: MTTR=%.0fs, confidence=%.2f, tools=%d",
            incident.id, total_mttr, metrics.confidence_score, metrics.tools_called,
        )
        return metrics

    def get_impact_summary(self, period_days: int = 30) -> ImpactSummary:
        """Compute aggregated impact summary for the given period."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)
        recent = [r for r in self._records if r.created_at >= cutoff]

        if not recent:
            return ImpactSummary(period_days=period_days)

        total = len(recent)
        avg_mttr = sum(r.total_mttr_seconds for r in recent) / total
        auto_resolved = sum(1 for r in recent if r.auto_remediated)
        requiring_human = total - auto_resolved

        # Calculate accuracy (only for records that have been reviewed)
        reviewed = [r for r in recent if r.was_correct is not None]
        accuracy = (sum(1 for r in reviewed if r.was_correct) / len(reviewed)) if reviewed else 0.0

        # Estimate hours saved: (manual_mttr - agent_mttr) * num_incidents / 3600
        manual_baseline = self.config.estimated_manual_mttr_seconds
        hours_saved = max(0, (manual_baseline - avg_mttr) * total / 3600)

        # Calculate MTTR trend (compare first half vs second half of period)
        mid = len(recent) // 2
        if mid > 0:
            first_half_avg = sum(r.total_mttr_seconds for r in recent[:mid]) / mid
            second_half_avg = sum(r.total_mttr_seconds for r in recent[mid:]) / (len(recent) - mid)
            if first_half_avg > 0:
                trend = ((second_half_avg - first_half_avg) / first_half_avg) * 100
            else:
                trend = 0.0
        else:
            trend = 0.0

        # Top patterns
        from collections import Counter
        severity_counts = Counter(r.severity.value for r in recent)
        top_patterns = [
            {"severity": sev, "count": count}
            for sev, count in severity_counts.most_common(5)
        ]

        return ImpactSummary(
            total_incidents=total,
            avg_mttr_seconds=round(avg_mttr, 1),
            mttr_trend_percent=round(trend, 1),
            incidents_auto_resolved=auto_resolved,
            incidents_requiring_human=requiring_human,
            accuracy_rate=round(accuracy, 3),
            estimated_hours_saved=round(hours_saved, 1),
            top_patterns=top_patterns,
            period_days=period_days,
        )

    def mark_correct(self, incident_id: str, was_correct: bool) -> bool:
        """Mark whether the agent's root cause identification was correct."""
        for record in self._records:
            if record.incident_id == incident_id:
                record.was_correct = was_correct
                self._save()
                return True
        return False

    @property
    def total_records(self) -> int:
        return len(self._records)
