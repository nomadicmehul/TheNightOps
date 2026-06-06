"""
Outcome Recorder for TheNightOps.

Closes the learning loop. After an investigation produces an RCA, this module:

1. Assembles a structured ``Investigation`` from the free-text result.
2. Classifies the incident pattern and asks the PolicyEngine for
   policy-evaluated remediation suggestions (advisory — nothing is executed).
3. Persists the outcome to incident memory (for future similarity matching)
   and to the metrics tracker (for MTTR / impact reporting).

Every write is guarded by its config flag and wrapped in try/except so a
storage failure can never break an in-flight investigation.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime

from nightops.core.config import NightOpsConfig
from nightops.core.models import (
    Finding,
    Incident,
    Investigation,
    RemediationAction,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ── RCA text parsing ─────────────────────────────────────────────────

_ROOT_CAUSE_RE = re.compile(
    r"\*{0,2}Root Cause\*{0,2}\s*[:\-]\s*(.+)", re.IGNORECASE
)
_CONFIDENCE_RE = re.compile(
    r"\*{0,2}Confidence(?:\s+Level)?\*{0,2}\s*[:\-]\s*(high|medium|low)",
    re.IGNORECASE,
)

_CONFIDENCE_MAP = {"high": 0.9, "medium": 0.6, "low": 0.3}


def parse_root_cause(result_text: str) -> str:
    """Extract the root-cause line from a structured RCA, else fall back to text."""
    match = _ROOT_CAUSE_RE.search(result_text)
    if match:
        line = match.group(1).strip()
        if line:
            return line
    # Fall back to the whole result (trimmed) so memory still has signal.
    return result_text.strip()[:1000]


def parse_confidence(result_text: str) -> float:
    """Extract a 0..1 confidence score from the RCA's confidence line."""
    match = _CONFIDENCE_RE.search(result_text)
    if match:
        return _CONFIDENCE_MAP.get(match.group(1).lower(), 0.5)
    return 0.5


# ── Assembly ─────────────────────────────────────────────────────────

def ensure_incident(
    incident: Incident | None,
    incident_description: str,
    incident_id: str | None,
) -> Incident:
    """Return the provided Incident, or synthesize a minimal one.

    Single-run / interactive mode only has a description string, so we build
    a lightweight Incident. Watch mode passes the real object through.
    """
    if incident is not None:
        return incident
    stripped = incident_description.strip()
    title = (stripped.splitlines()[0][:120] if stripped else "") or "Investigation"
    return Incident(
        id=incident_id or f"inc-{uuid.uuid4().hex[:8]}",
        title=title or "Investigation",
        description=incident_description,
        source="manual",
    )


def build_investigation(
    incident: Incident,
    *,
    started_at: datetime,
    completed_at: datetime,
    result_text: str,
    tools_called: int,
    matched_ids: list[str] | None = None,
) -> Investigation:
    """Assemble a structured Investigation from a free-text RCA result."""
    confidence = parse_confidence(result_text)
    # One summary finding carries the full RCA text so downstream pattern
    # detection (which scans findings) has maximum signal to work with.
    summary_finding = Finding(
        source=incident.source or "orchestrator",
        category="rca_summary",
        description=result_text.strip(),
        confidence=confidence,
        timestamp=completed_at,
    )
    return Investigation(
        incident=incident,
        started_at=started_at,
        completed_at=completed_at,
        findings=[summary_finding],
        root_cause=parse_root_cause(result_text),
        root_cause_confidence=confidence,
        rca_draft=result_text.strip(),
        matched_historical_incidents=matched_ids or [],
        tools_called=tools_called,
    )


# ── Remediation (advisory, policy-evaluated) ─────────────────────────

def evaluate_remediations(
    config: NightOpsConfig,
    investigation: Investigation,
) -> list[RemediationAction]:
    """Return policy-evaluated remediation suggestions for the incident.

    Advisory only: each action is classified (auto-approved / needs-approval /
    blocked) by the PolicyEngine. Nothing is executed.
    """
    if not config.remediation.enabled:
        return []
    try:
        from nightops.intelligence.incident_memory import detect_pattern_type
        from nightops.remediation.policy_engine import PolicyEngine

        pattern_type = detect_pattern_type(investigation.rca_draft or investigation.root_cause)
        engine = PolicyEngine(config.remediation.policy_path)
        environment = investigation.incident.environment or "production"
        return engine.get_suggested_remediations(
            pattern_type,
            environment=environment,
            namespace=investigation.incident.namespace,
        )
    except Exception:
        logger.exception("Failed to evaluate remediation suggestions")
        return []


def _action_status(action: RemediationAction) -> str:
    if action.result.startswith("BLOCKED"):
        return "BLOCKED"
    if action.auto_approved:
        return "AUTO-APPROVED"
    return "NEEDS APPROVAL"


def render_remediations(actions: list[RemediationAction]) -> str:
    """Render evaluated remediation actions as a markdown section, or ''."""
    if not actions:
        return ""
    lines = ["## Suggested Remediations (policy-evaluated, advisory)"]
    for a in actions:
        lines.append(
            f"- **[{_action_status(a)}]** `{a.action_type}` — {a.description} "
            f"(confidence {a.confidence:.0%})"
        )
    return "\n".join(lines)


# ── Persistence ──────────────────────────────────────────────────────

def record_outcome(config: NightOpsConfig, investigation: Investigation) -> None:
    """Persist a completed investigation to incident memory and metrics.

    Each store is flag-gated and isolated: a failure in one never affects the
    other or the caller.
    """
    if config.intelligence.enabled:
        try:
            from nightops.intelligence.incident_memory import IncidentMemory

            IncidentMemory(config.intelligence).record_investigation(investigation)
        except Exception:
            logger.exception("Failed to record investigation to incident memory")

    if config.metrics.enabled:
        try:
            from nightops.metrics.tracker import MetricsTracker

            MetricsTracker(config.metrics).record_investigation(investigation)
        except Exception:
            logger.exception("Failed to record investigation metrics")


def finalize_investigation(
    config: NightOpsConfig,
    *,
    incident: Incident | None,
    incident_description: str,
    incident_id: str | None,
    started_at: datetime,
    result_text: str,
    tools_called: int,
    matched_ids: list[str] | None = None,
) -> str:
    """End-to-end write-back for a successful investigation.

    Builds the Investigation, evaluates advisory remediations, persists the
    outcome, and returns a markdown remediation section to append to the
    result (empty string if none). Fully fail-safe.
    """
    try:
        inc = ensure_incident(incident, incident_description, incident_id)
        investigation = build_investigation(
            inc,
            started_at=started_at,
            completed_at=_utcnow(),
            result_text=result_text,
            tools_called=tools_called,
            matched_ids=matched_ids,
        )
        actions = evaluate_remediations(config, investigation)
        investigation.remediation_actions = actions
        record_outcome(config, investigation)
        return render_remediations(actions)
    except Exception:
        logger.exception("finalize_investigation failed (non-fatal)")
        return ""
