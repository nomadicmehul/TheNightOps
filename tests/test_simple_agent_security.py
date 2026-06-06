"""Security tests for Plan B (simple kubectl agent) — the Secret denylist.

Secrets must never reach the model context. These tests verify the denylist
blocks Secret reads at both the tool level and the _run_kubectl level, while
leaving legitimate (ConfigMap, Deployment, etc.) inspection untouched.
"""

from __future__ import annotations

import pytest

from nightops.agents import simple_agent
from nightops.agents.simple_agent import (
    _is_denied_resource,
    kubectl_get_resource_yaml,
)


@pytest.mark.parametrize(
    "kind",
    ["secret", "secrets", "Secret", "SECRET", "secrets.v1.", "secret.example.com"],
)
def test_denied_resource_kinds(kind):
    assert _is_denied_resource(kind) is True


@pytest.mark.parametrize(
    "kind",
    ["configmap", "configmaps", "deployment", "pod", "pods", "service", "node"],
)
def test_allowed_resource_kinds(kind):
    assert _is_denied_resource(kind) is False


def test_get_resource_yaml_blocks_secret_without_calling_kubectl(monkeypatch):
    called = {"ran": False}

    def _fake_run(*_a, **_k):
        called["ran"] = True
        return "should not happen"

    monkeypatch.setattr(simple_agent, "_run_kubectl", _fake_run)
    out = kubectl_get_resource_yaml("secret", "db-credentials", "production")
    assert "blocked by policy" in out
    assert called["ran"] is False  # short-circuits before shelling out


def test_run_kubectl_blocks_get_secret(monkeypatch):
    # subprocess must never be invoked for a denied kind
    def _boom(*_a, **_k):
        raise AssertionError("subprocess.run should not be called for Secrets")

    monkeypatch.setattr(simple_agent.subprocess, "run", _boom)
    out = simple_agent._run_kubectl(["get", "secrets", "-n", "default", "-o", "yaml"])
    assert "blocked by policy" in out


def test_run_kubectl_allows_namespace_named_secret(monkeypatch):
    # A namespace that happens to be named "secret" must NOT be blocked:
    # the denylist only inspects the resource-kind position (args[1]).
    captured = {}

    class _Result:
        returncode = 0
        stdout = "pod-list-output"
        stderr = ""

    def _fake_run(cmd, **_k):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(simple_agent.subprocess, "run", _fake_run)
    out = simple_agent._run_kubectl(["get", "pods", "-n", "secret"])
    assert out == "pod-list-output"
    assert captured["cmd"] == ["kubectl", "get", "pods", "-n", "secret"]
