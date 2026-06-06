"""
Alert Deduplication for TheNightOps.

Uses a sliding window + fingerprinting approach to collapse
multiple identical alerts into a single incident.

Example: If 50 pods fire the same OOMKill alert within a 5-minute window,
that's 1 incident — not 50.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from nightops.core.models import AlertGroup, WebhookAlert

logger = logging.getLogger(__name__)


class AlertDeduplicator:
    """Deduplicates alerts using fingerprint-based sliding windows."""

    def __init__(
        self,
        window_seconds: int = 300,
        max_groups: int = 1000,
    ):
        self.window_seconds = window_seconds
        self.max_groups = max_groups
        self._groups: dict[str, AlertGroup] = {}

    def check_and_add(self, alert: WebhookAlert) -> bool:
        """Check if an alert is new (returns True) or a duplicate (returns False).

        If the alert's fingerprint matches an existing group within the
        dedup window, it's considered a duplicate. Otherwise, a new group
        is created.
        """
        self._cleanup_expired()

        fingerprint = alert.fingerprint or alert.compute_fingerprint()
        now_ts = time.time()

        if fingerprint in self._groups:
            group = self._groups[fingerprint]
            group.alerts.append(alert)
            group.last_seen = alert.starts_at or _utcnow()
            group.count += 1
            logger.debug(
                "Dedup: alert '%s' merged into group %s (count: %d)",
                alert.alert_name, fingerprint, group.count,
            )
            return False

        # New alert group
        group = AlertGroup(
            fingerprint=fingerprint,
            alerts=[alert],
            first_seen=alert.starts_at or _utcnow(),
            last_seen=alert.starts_at or _utcnow(),
            count=1,
        )
        self._groups[fingerprint] = group
        logger.info(
            "Dedup: new alert group created for '%s' (fingerprint: %s)",
            alert.alert_name, fingerprint,
        )
        return True

    def resolve(self, alert: WebhookAlert) -> Optional[AlertGroup]:
        """Mark an alert group as resolved and remove it from active tracking."""
        fingerprint = alert.fingerprint or alert.compute_fingerprint()
        group = self._groups.pop(fingerprint, None)
        if group:
            logger.info(
                "Dedup: resolved alert group '%s' (had %d alerts)",
                fingerprint, group.count,
            )
        return group

    def get_group(self, fingerprint: str) -> Optional[AlertGroup]:
        """Get an active alert group by fingerprint."""
        return self._groups.get(fingerprint)

    def get_active_groups(self) -> list[AlertGroup]:
        """Return all active (non-expired) alert groups."""
        self._cleanup_expired()
        return list(self._groups.values())

    def _cleanup_expired(self) -> None:
        """Remove alert groups older than the dedup window."""
        now_ts = time.time()
        expired = [
            fp for fp, group in self._groups.items()
            if (now_ts - group.last_seen.timestamp()) > self.window_seconds
        ]
        for fp in expired:
            del self._groups[fp]

        # Enforce max groups limit (evict oldest)
        if len(self._groups) > self.max_groups:
            sorted_groups = sorted(
                self._groups.items(),
                key=lambda x: x[1].first_seen.timestamp(),
            )
            excess = len(self._groups) - self.max_groups
            for fp, _ in sorted_groups[:excess]:
                del self._groups[fp]


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
