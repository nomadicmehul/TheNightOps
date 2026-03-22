"""
Incident Memory for TheNightOps.

Stores resolved incidents and their RCA data, enabling the agent to
learn from past incidents and find similar patterns faster.

Uses a lightweight local JSON store with TF-IDF similarity matching.
No external dependencies required (no ChromaDB, no vector DB).

The agent gets smarter with every resolved incident.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from nightops.core.config import IntelligenceConfig
from nightops.core.models import (
    IncidentRecord,
    Investigation,
    SimilarIncident,
)

logger = logging.getLogger(__name__)


class IncidentMemory:
    """Stores and retrieves historical incident data for pattern matching."""

    def __init__(self, config: IntelligenceConfig):
        self.config = config
        self.store_path = Path(config.store_path)
        self.store_path.mkdir(parents=True, exist_ok=True)
        self._records_file = self.store_path / "incidents.json"
        self._records: list[IncidentRecord] = []
        self._load()

    def _load(self) -> None:
        """Load incident records from disk."""
        if self._records_file.exists():
            try:
                data = json.loads(self._records_file.read_text())
                self._records = [IncidentRecord(**r) for r in data]
                logger.info("Loaded %d historical incident records", len(self._records))
            except Exception:
                logger.exception("Failed to load incident records, starting fresh")
                self._records = []

    def _save(self) -> None:
        """Persist incident records to disk."""
        data = [r.model_dump(mode="json") for r in self._records]
        self._records_file.write_text(json.dumps(data, indent=2, default=str))

    def record_investigation(self, investigation: Investigation) -> IncidentRecord:
        """Record a completed investigation for future pattern matching."""
        incident = investigation.incident

        # Calculate MTTR
        mttr = 0.0
        if investigation.completed_at and investigation.started_at:
            mttr = (investigation.completed_at - investigation.started_at).total_seconds()

        # Build findings summary
        findings_summary = " | ".join(
            f"{f.category}: {f.description}" for f in investigation.findings[:10]
        )

        # Determine pattern type from findings
        pattern_type = _detect_pattern_type(investigation)

        record = IncidentRecord(
            incident_id=incident.id,
            title=incident.title,
            service_name=incident.service_name,
            root_cause=investigation.root_cause,
            resolution=investigation.rca_draft[:500] if investigation.rca_draft else "",
            severity=incident.severity,
            environment=incident.environment,
            cluster=incident.cluster,
            namespace=incident.namespace,
            pattern_type=pattern_type,
            mttr_seconds=mttr,
            findings_summary=findings_summary,
            action_items=investigation.recommendations[:5],
            created_at=investigation.started_at,
            resolved_at=investigation.completed_at,
        )
        record.embedding_text = record.build_embedding_text()

        self._records.append(record)
        self._save()

        logger.info(
            "Recorded incident %s (pattern: %s, MTTR: %.0fs) — total records: %d",
            incident.id, pattern_type, mttr, len(self._records),
        )
        return record

    def find_similar(
        self,
        query: str,
        service_name: str = "",
        max_results: int | None = None,
    ) -> list[SimilarIncident]:
        """Find similar historical incidents using TF-IDF text similarity."""
        if not self._records:
            return []

        max_results = max_results or self.config.max_similar_results

        # Build query text
        query_text = query
        if service_name:
            query_text = f"Service: {service_name} | {query}"

        # TF-IDF similarity
        scored = []
        query_tokens = _tokenize(query_text)
        query_tf = _term_frequency(query_tokens)

        # Build IDF from corpus
        all_docs = [_tokenize(r.embedding_text or r.build_embedding_text()) for r in self._records]
        idf = _inverse_document_frequency(all_docs + [query_tokens])

        query_vec = {t: tf * idf.get(t, 0) for t, tf in query_tf.items()}

        for i, record in enumerate(self._records):
            doc_tokens = all_docs[i]
            doc_tf = _term_frequency(doc_tokens)
            doc_vec = {t: tf * idf.get(t, 0) for t, tf in doc_tf.items()}

            similarity = _cosine_similarity(query_vec, doc_vec)
            if similarity >= self.config.similarity_threshold:
                scored.append((similarity, record))

        # Sort by similarity descending
        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, record in scored[:max_results]:
            results.append(SimilarIncident(
                incident_id=record.incident_id,
                title=record.title,
                root_cause=record.root_cause,
                resolution=record.resolution,
                similarity_score=round(score, 3),
                mttr_seconds=record.mttr_seconds,
            ))

        return results

    def get_pattern_stats(self) -> dict[str, int]:
        """Get count of incidents by pattern type."""
        counter: Counter = Counter()
        for r in self._records:
            counter[r.pattern_type or "unknown"] += 1
        return dict(counter.most_common())

    @property
    def total_records(self) -> int:
        return len(self._records)


def _detect_pattern_type(investigation: Investigation) -> str:
    """Detect the incident pattern type from findings."""
    text = " ".join(
        f.description.lower() for f in investigation.findings
    ) + " " + investigation.root_cause.lower()

    patterns = {
        "oom_kill": ["oomkilled", "oom", "out of memory", "memory limit"],
        "cpu_spike": ["cpu", "throttl", "high cpu", "cpu exhaustion"],
        "cascading_failure": ["cascading", "timeout", "connection pool", "circuit break"],
        "config_drift": ["config", "configmap", "environment variable", "drift"],
        "crashloop": ["crashloopbackoff", "crash loop", "backoff"],
        "disk_pressure": ["disk", "storage", "volume", "pvc"],
        "network": ["network", "dns", "connection refused", "unreachable"],
        "deployment_failure": ["rollout", "deploy", "image pull", "imagepullbackoff"],
    }

    for pattern_name, keywords in patterns.items():
        if any(kw in text for kw in keywords):
            return pattern_name

    return "unknown"


def _tokenize(text: str) -> list[str]:
    """Simple tokenizer: lowercase, split on non-alpha, remove short tokens."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if len(t) > 2]


def _term_frequency(tokens: list[str]) -> dict[str, float]:
    """Compute normalized term frequency."""
    counter = Counter(tokens)
    total = len(tokens) or 1
    return {t: c / total for t, c in counter.items()}


def _inverse_document_frequency(documents: list[list[str]]) -> dict[str, float]:
    """Compute IDF across all documents."""
    n = len(documents)
    df: Counter = Counter()
    for doc in documents:
        unique_terms = set(doc)
        for term in unique_terms:
            df[term] += 1

    return {t: math.log((n + 1) / (count + 1)) + 1 for t, count in df.items()}


def _cosine_similarity(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    """Compute cosine similarity between two sparse vectors."""
    common = set(vec_a.keys()) & set(vec_b.keys())
    if not common:
        return 0.0

    dot = sum(vec_a[t] * vec_b[t] for t in common)
    mag_a = math.sqrt(sum(v ** 2 for v in vec_a.values()))
    mag_b = math.sqrt(sum(v ** 2 for v in vec_b.values()))

    if mag_a == 0 or mag_b == 0:
        return 0.0

    return dot / (mag_a * mag_b)
