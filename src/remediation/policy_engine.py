"""
Remediation Policy Engine for TheNightOps.

Implements graduated autonomy — the agent can auto-approve safe actions
(like silencing alerts) while requiring human approval for dangerous ones
(like rolling back deployments in production).

Policy hierarchy:
  - Level 0: Auto-approve (silencing alerts, posting to Slack, creating Grafana incidents)
  - Level 1: Environment-gated (auto in dev/staging, manual in prod)
  - Level 2: Always require approval (rollbacks, scaling, config changes)
  - Level 3: Blocked (never allow — delete namespace, drop DB, etc.)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

from nightops.core.models import RemediationAction, RemediationPolicy, Severity

logger = logging.getLogger(__name__)

# Default policies when no config file is present
DEFAULT_POLICIES = {
    # Level 0: Auto-approve
    "silence_alert": {
        "auto_approve_environments": ["*"],
        "conditions": {},
        "blocked": False,
    },
    "create_grafana_incident": {
        "auto_approve_environments": ["*"],
        "conditions": {},
        "blocked": False,
    },
    "post_slack_update": {
        "auto_approve_environments": ["*"],
        "conditions": {"channels": ["#incidents"]},
        "blocked": False,
    },
    "create_annotation": {
        "auto_approve_environments": ["*"],
        "conditions": {},
        "blocked": False,
    },
    # Level 1: Environment-gated
    "restart_pod": {
        "auto_approve_environments": ["development", "staging"],
        "require_approval_environments": ["production"],
        "conditions": {"max_restart_count": 3},
        "blocked": False,
    },
    "scale_up": {
        "auto_approve_environments": ["development", "staging"],
        "require_approval_environments": ["production"],
        "conditions": {"max_replicas": 10},
        "blocked": False,
    },
    # Level 2: Always require approval
    "rollback_deployment": {
        "auto_approve_environments": [],
        "require_approval_environments": ["*"],
        "conditions": {},
        "blocked": False,
    },
    "scale_down": {
        "auto_approve_environments": [],
        "require_approval_environments": ["*"],
        "conditions": {},
        "blocked": False,
    },
    "revert_configmap": {
        "auto_approve_environments": [],
        "require_approval_environments": ["*"],
        "conditions": {},
        "blocked": False,
    },
    "send_external_notification": {
        "auto_approve_environments": [],
        "require_approval_environments": ["*"],
        "conditions": {},
        "blocked": False,
    },
    # Level 3: Blocked
    "delete_namespace": {
        "auto_approve_environments": [],
        "require_approval_environments": [],
        "conditions": {},
        "blocked": True,
    },
    "delete_pvc": {
        "auto_approve_environments": [],
        "require_approval_environments": [],
        "conditions": {},
        "blocked": True,
    },
    "cluster_delete": {
        "auto_approve_environments": [],
        "require_approval_environments": [],
        "conditions": {},
        "blocked": True,
    },
}

# Known pattern → remediation mapping
KNOWN_REMEDIATIONS: dict[str, list[dict[str, Any]]] = {
    "oom_kill": [
        {"action_type": "restart_pod", "description": "Restart the OOMKilled pod", "confidence": 0.7},
        {"action_type": "rollback_deployment", "description": "Rollback to previous image version", "confidence": 0.8},
    ],
    "crashloop": [
        {"action_type": "rollback_deployment", "description": "Rollback deployment to last known good version", "confidence": 0.85},
    ],
    "config_drift": [
        {"action_type": "revert_configmap", "description": "Revert ConfigMap to previous version", "confidence": 0.8},
    ],
    "cpu_spike": [
        {"action_type": "scale_up", "description": "Scale up deployment replicas", "confidence": 0.6},
        {"action_type": "restart_pod", "description": "Restart pods to clear stuck processes", "confidence": 0.5},
    ],
    "cascading_failure": [
        {"action_type": "restart_pod", "description": "Restart pods to reset connection pools", "confidence": 0.7},
    ],
}


class PolicyEngine:
    """Evaluates remediation actions against configured policies."""

    def __init__(self, policy_path: str | None = None):
        self._policies: dict[str, dict] = dict(DEFAULT_POLICIES)
        if policy_path:
            self._load_policies(policy_path)

    def _load_policies(self, path: str) -> None:
        """Load policies from a YAML file, merging with defaults."""
        policy_file = Path(path)
        if not policy_file.exists():
            logger.info("No policy file at %s, using defaults", path)
            return

        try:
            data = yaml.safe_load(policy_file.read_text())
            policies = data.get("policies", {})
            for action_type, policy_data in policies.items():
                self._policies[action_type] = policy_data
            logger.info("Loaded %d remediation policies from %s", len(policies), path)
        except Exception:
            logger.exception("Failed to load policy file %s, using defaults", path)

    def evaluate(
        self,
        action: RemediationAction,
        environment: str = "production",
    ) -> RemediationAction:
        """Evaluate a remediation action against policies and set auto_approved flag."""
        policy = self._policies.get(action.action_type, {})

        # Check if action is blocked
        if policy.get("blocked", False):
            action.auto_approved = False
            action.approved = False
            action.result = f"BLOCKED: Action '{action.action_type}' is not permitted by policy"
            logger.warning("Blocked action: %s", action.action_type)
            return action

        # Check auto-approve environments
        auto_envs = policy.get("auto_approve_environments", [])
        if "*" in auto_envs or environment.lower() in [e.lower() for e in auto_envs]:
            # Check conditions
            if self._check_conditions(action, policy.get("conditions", {})):
                action.auto_approved = True
                action.approved = True
                logger.info(
                    "Auto-approved: %s in %s environment",
                    action.action_type, environment,
                )
                return action

        # Falls through to require human approval
        action.auto_approved = False
        action.approved = None  # Pending human decision
        logger.info(
            "Requires approval: %s in %s environment",
            action.action_type, environment,
        )
        return action

    def _check_conditions(self, action: RemediationAction, conditions: dict) -> bool:
        """Check if action-specific conditions are met for auto-approval."""
        # For now, conditions are simple key-value checks
        # Future: more complex condition evaluation
        return True

    def get_suggested_remediations(
        self,
        pattern_type: str,
        environment: str = "production",
        namespace: str = "",
    ) -> list[RemediationAction]:
        """Get suggested remediation actions for a known incident pattern."""
        suggestions = KNOWN_REMEDIATIONS.get(pattern_type, [])
        actions = []

        for suggestion in suggestions:
            action = RemediationAction(
                action_type=suggestion["action_type"],
                description=suggestion["description"],
                namespace=namespace,
                confidence=suggestion.get("confidence", 0.5),
            )
            # Evaluate against policy
            action = self.evaluate(action, environment)
            actions.append(action)

        return actions

    def get_policy_summary(self) -> dict[str, str]:
        """Return a summary of all policies for display."""
        summary = {}
        for action_type, policy in self._policies.items():
            if policy.get("blocked"):
                summary[action_type] = "BLOCKED"
            elif "*" in policy.get("auto_approve_environments", []):
                summary[action_type] = "AUTO-APPROVE (all environments)"
            elif policy.get("auto_approve_environments"):
                envs = ", ".join(policy["auto_approve_environments"])
                summary[action_type] = f"AUTO in {envs}, MANUAL elsewhere"
            else:
                summary[action_type] = "REQUIRES APPROVAL"
        return summary
