"""Tests for the Kubernetes event watcher.

Focus on the two stability-critical behaviors:
1. Events are processed incrementally (not buffered into batches).
2. Each configured namespace is watched by its own concurrent task.

The kubernetes client is faked, so these run without a cluster.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from nightops.core.config import EventWatcherConfig
from nightops.core.models import Incident
from nightops.ingestion.deduplication import AlertDeduplicator
from nightops.ingestion.event_watcher import EventWatcher


def _make_event(reason: str, pod: str = "checkout-api-abc123-xyz", ns: str = "default"):
    """Build a fake watch event matching the kubernetes client object shape."""
    involved = SimpleNamespace(name=pod, namespace=ns, kind="Pod")
    obj = SimpleNamespace(
        reason=reason,
        message=f"{reason} happened",
        type="Warning",
        involved_object=involved,
    )
    return {"type": "ADDED", "object": obj}


class _FakeWatch:
    """Mimics kubernetes.watch.Watch — yields preloaded events then ends."""

    def __init__(self, events: list[dict]):
        self._events = events
        self.stopped = False

    def stream(self, _func, **_kwargs):
        yield from self._events

    def stop(self):
        self.stopped = True


@pytest.fixture
def collected():
    incidents: list[Incident] = []

    async def on_new_incident(inc: Incident) -> None:
        incidents.append(inc)

    return incidents, on_new_incident


@pytest.fixture
def watcher(collected):
    incidents, cb = collected
    config = EventWatcherConfig(namespaces=["default"])
    dedup = AlertDeduplicator()
    return EventWatcher(config=config, deduplicator=dedup, on_new_incident=cb)


@pytest.mark.asyncio
async def test_handle_event_creates_incident_for_watched_reason(watcher, collected):
    incidents, _ = collected
    await watcher._handle_event(_make_event("OOMKilled"))
    assert len(incidents) == 1
    assert "OOMKilled" in incidents[0].title
    assert incidents[0].service_name == "checkout-api"  # pod hash stripped
    assert watcher._events_processed == 1


@pytest.mark.asyncio
async def test_handle_event_ignores_normal_events(watcher, collected):
    incidents, _ = collected
    ev = _make_event("OOMKilled")
    ev["object"].type = "Normal"  # not a Warning
    await watcher._handle_event(ev)
    assert incidents == []


@pytest.mark.asyncio
async def test_handle_event_ignores_unwatched_reasons(watcher, collected):
    incidents, _ = collected
    await watcher._handle_event(_make_event("SomeRandomReason"))
    assert incidents == []


@pytest.mark.asyncio
async def test_handle_event_dedupes_identical_events(watcher, collected):
    incidents, _ = collected
    await watcher._handle_event(_make_event("OOMKilled"))
    await watcher._handle_event(_make_event("OOMKilled"))  # same fingerprint
    assert len(incidents) == 1


@pytest.mark.asyncio
async def test_watch_namespace_processes_events_incrementally(watcher, collected):
    incidents, _ = collected
    watcher._running = True
    events = [_make_event("OOMKilled", pod="a-1"), _make_event("CrashLoopBackOff", pod="b-2")]
    fake_watch = _FakeWatch(events)

    class _FakeWatchModule:
        @staticmethod
        def Watch():  # noqa: N802 — mirrors kubernetes.watch.Watch
            return fake_watch

    # The fake stream ends after yielding, so the loop reconnects; stop after one pass.
    async def stop_soon():
        await asyncio.sleep(0.05)
        watcher._running = False

    asyncio.create_task(stop_soon())
    fake_v1 = SimpleNamespace(list_namespaced_event=lambda *a, **k: None)
    await watcher._watch_namespace(v1=fake_v1, k8s_watch=_FakeWatchModule(), namespace="default")

    assert len(incidents) == 2
    assert fake_watch.stopped is True  # stream cleaned up in finally
