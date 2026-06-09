"""Tests for per-agent MCP tool scoping (least privilege).

Verifies that:
1. Toolsets are grouped into the right capability categories.
2. Read-only investigation agents never receive send-capable (Slack/Notifications)
   tools, and each agent only gets the categories its prompt actually uses.
"""

from __future__ import annotations

import pytest

from nightops.agents.root_orchestrator import (
    create_categorized_toolsets,
    create_root_orchestrator,
)
from nightops.core.config import NightOpsConfig


@pytest.fixture
def local_config():
    """Local mode: custom MCP servers on, official GCP + Grafana off."""
    config = NightOpsConfig()
    config.gke.enabled = False
    config.cloud_observability.enabled = False
    config.grafana.enabled = False
    config.kubernetes.enabled = True
    config.cloud_logging_custom.enabled = True
    config.slack.enabled = True
    config.notifications.enabled = True
    return config


def test_categorization_groups_servers_correctly(local_config):
    cats = create_categorized_toolsets(local_config)
    assert len(cats["kubernetes"]) == 1  # custom k8s
    assert len(cats["logging"]) == 1  # custom cloud logging
    assert len(cats["grafana"]) == 0  # disabled
    assert len(cats["notifications"]) == 2  # slack + notifications


def _by_name(agent, name):
    return next(a for a in agent.sub_agents if a.name == name)


def test_agents_are_scoped_to_their_domains(local_config):
    root = create_root_orchestrator(local_config)

    log_analyst = _by_name(root, "log_analyst")
    deployment_correlator = _by_name(root, "deployment_correlator")
    anomaly_detector = _by_name(root, "anomaly_detector")
    runbook_retriever = _by_name(root, "runbook_retriever")
    communication_drafter = _by_name(root, "communication_drafter")

    assert len(log_analyst.tools) == 1  # logging only
    assert len(deployment_correlator.tools) == 1  # kubernetes only
    assert len(anomaly_detector.tools) == 2  # kubernetes + logging
    assert len(runbook_retriever.tools) == 2  # kubernetes + logging (+ grafana=0)
    assert len(communication_drafter.tools) == 0  # advisory, no tools
    assert len(root.tools) == 2  # kubernetes + logging for triage


def test_notification_tools_never_reach_any_agent(local_config):
    """Slack/Notifications (send capability) must be wired to no agent."""
    root = create_root_orchestrator(local_config)

    used: set[int] = set()
    for agent in [root, *root.sub_agents]:
        for ts in agent.tools:
            used.add(id(ts))

    # Only the kubernetes(1) + logging(1) toolsets are ever used; the two
    # notification toolsets are created but attached to nobody.
    assert len(used) == 2
