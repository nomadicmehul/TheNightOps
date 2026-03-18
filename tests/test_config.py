"""Tests for configuration loading."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nightops.core.config import NightOpsConfig


def test_default_config():
    """Test that default configuration loads without errors."""
    config = NightOpsConfig()
    assert config.agent.model == "gemini-2.5-flash"
    # Official Google Cloud MCP servers
    assert config.cloud_observability.type == "official"
    assert config.gke.type == "official"
    # Official Grafana MCP
    assert config.grafana.type == "official"
    assert config.grafana.transport == "stdio"
    # Custom MCP servers
    assert config.slack.port == 8004
    assert config.notifications.port == 8005


def test_official_mcp_defaults():
    """Test that official Google Cloud MCP servers have correct defaults."""
    config = NightOpsConfig()
    assert "logging.googleapis.com" in config.cloud_observability.endpoint
    assert "container.googleapis.com" in config.gke.endpoint
    assert config.cloud_observability.auth == "iam"
    assert config.gke.auth == "iam"


def test_config_from_yaml(tmp_path: Path):
    """Test loading configuration from a YAML file."""
    yaml_content = """
agent:
  model: gemini-1.5-pro
  max_investigation_time: 600

cloud_observability:
  project_id: test-project

gke:
  project_id: test-project
  cluster: test-cluster
  location: us-central1-a

slack:
  bot_token: xoxb-test
  port: 9004

notifications:
  port: 9005
"""
    config_file = tmp_path / "nightops.yaml"
    config_file.write_text(yaml_content)

    config = NightOpsConfig.from_yaml(config_file)
    assert config.agent.model == "gemini-1.5-pro"
    assert config.agent.max_investigation_time == 600
    assert config.cloud_observability.project_id == "test-project"
    assert config.gke.cluster == "test-cluster"
    assert config.slack.bot_token == "xoxb-test"
    assert config.notifications.port == 9005


def test_config_with_mcp_servers_key(tmp_path: Path):
    """Test loading configuration with nested mcp_servers key."""
    yaml_content = """
agent:
  model: gemini-2.0-flash

mcp_servers:
  cloud_observability:
    project_id: nested-project
  gke:
    project_id: nested-project
    cluster: nested-cluster
    location: us-east1-b
  slack:
    bot_token: xoxb-nested
  notifications:
    port: 9005
"""
    config_file = tmp_path / "nightops.yaml"
    config_file.write_text(yaml_content)

    config = NightOpsConfig.from_yaml(config_file)
    assert config.cloud_observability.project_id == "nested-project"
    assert config.gke.cluster == "nested-cluster"
    assert config.slack.bot_token == "xoxb-nested"
    assert config.notifications.port == 9005


def test_config_env_substitution(tmp_path: Path):
    """Test environment variable substitution in YAML."""
    os.environ["TEST_PROJECT_ID"] = "my-project-123"

    yaml_content = """
cloud_observability:
  project_id: ${TEST_PROJECT_ID}
gke:
  project_id: ${TEST_PROJECT_ID}
"""
    config_file = tmp_path / "nightops.yaml"
    config_file.write_text(yaml_content)

    config = NightOpsConfig.from_yaml(config_file)
    assert config.cloud_observability.project_id == "my-project-123"
    assert config.gke.project_id == "my-project-123"

    del os.environ["TEST_PROJECT_ID"]


def test_config_missing_file():
    """Test that missing config file raises error."""
    with pytest.raises(FileNotFoundError):
        NightOpsConfig.from_yaml("/nonexistent/path.yaml")


def test_human_approval_defaults():
    """Test that human approval defaults are set correctly."""
    config = NightOpsConfig()
    assert "pod_restart" in config.agent.require_human_approval
    assert "deployment_rollback" in config.agent.require_human_approval
    assert "external_notification" in config.agent.require_human_approval


def test_hybrid_mcp_architecture():
    """Test that config correctly represents the hybrid MCP architecture."""
    config = NightOpsConfig(
        cloud_observability={"project_id": "my-proj"},
        gke={"project_id": "my-proj", "cluster": "my-cluster", "location": "us-central1"},
        slack={"bot_token": "xoxb-token", "port": 8004},
        notifications={"port": 8005},
    )
    # Official servers use IAM auth
    assert config.cloud_observability.type == "official"
    assert config.gke.type == "official"
    # Custom servers use SSE transport
    assert config.slack.type == "custom"
    assert config.slack.transport == "sse"
    assert config.notifications.type == "custom"
    assert config.notifications.transport == "sse"
