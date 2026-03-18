# Changelog

All notable changes to TheNightOps will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0b1] - 2026-03-18

### Added
- GitHub Actions CI workflow (lint + test matrix on Python 3.11/3.12/3.13)
- GitHub Actions release workflow (PyPI trusted publisher + GitHub Releases)
- Dynamic version management via `src/__init__.py`
- CHANGELOG.md
- Beta version tagging (PEP 440)

## [0.3.0] - 2025-05-15

### Added
- Incident Memory with TF-IDF similarity matching
- Commons Clause license for source-available protection
- Static website for GitHub Pages
- BETA badge and sponsor section

## [0.2.0] - 2025-04-01

### Added
- Initial multi-agent SRE system
- Root orchestrator with 5 sub-agents
- MCP server integrations (GKE, Cloud Logging)
- Webhook ingestion pipeline
- Policy-based remediation engine
- Real-time investigation dashboard
- 5 demo failure scenarios
