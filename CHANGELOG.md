# Changelog

All notable changes to TheNightOps will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0b1] - 2026-03-18

First public beta release.

### Added
- Multi-agent SRE system with Root Orchestrator and 5 sub-agents
- Google ADK + Gemini integration (gemini-3.1-pro-preview default)
- Official GCP MCP servers (GKE, Cloud Observability) + local mode
- Webhook ingestion pipeline (Grafana, Alertmanager, PagerDuty, generic)
- Fingerprint-based alert deduplication
- Incident Memory with TF-IDF similarity matching
- Policy-based remediation engine (4 approval levels)
- Real-time investigation dashboard (FastAPI + WebSocket)
- Proactive anomaly detection scheduler
- MTTR and impact metrics tracking
- 5 demo failure scenarios (memory-leak, cpu-spike, cascading-failure, config-drift, oom-kill)
- Typer CLI with commands: agent, verify, metrics, policies, auto-config, demo, dashboard
- GitHub Actions CI workflow (lint + test on Python 3.11/3.12/3.13)
- GitHub Actions release workflow (PyPI trusted publisher + GitHub Releases)
- Static website for GitHub Pages
