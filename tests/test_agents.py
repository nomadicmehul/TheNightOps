"""Tests for agent creation."""

from __future__ import annotations

import pytest

from nightops.agents.log_analyst import create_log_analyst_agent
from nightops.agents.deployment_correlator import create_deployment_correlator_agent
from nightops.agents.runbook_retriever import create_runbook_retriever_agent
from nightops.agents.communication_drafter import create_communication_drafter_agent


def test_log_analyst_agent_creation():
    """Test that log analyst agent is created with correct properties."""
    agent = create_log_analyst_agent()
    assert agent.name == "log_analyst"
    assert "gemini" in agent.model
    assert "Log Analyst" in agent.instruction


def test_deployment_correlator_agent_creation():
    """Test that deployment correlator agent is created correctly."""
    agent = create_deployment_correlator_agent()
    assert agent.name == "deployment_correlator"
    assert "deployment" in agent.description.lower()


def test_runbook_retriever_agent_creation():
    """Test that runbook retriever agent is created correctly."""
    agent = create_runbook_retriever_agent()
    assert agent.name == "runbook_retriever"
    assert "alerts" in agent.description.lower() or "runbook" in agent.description.lower()


def test_communication_drafter_agent_creation():
    """Test that communication drafter agent is created correctly."""
    agent = create_communication_drafter_agent()
    assert agent.name == "communication_drafter"
    assert "communication" in agent.description.lower()


def test_custom_model():
    """Test agents with custom model."""
    agent = create_log_analyst_agent(model="gemini-1.5-pro")
    assert agent.model == "gemini-1.5-pro"
