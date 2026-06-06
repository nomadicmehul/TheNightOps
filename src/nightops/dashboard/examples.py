"""
TheNightOps Dashboard - Integration Examples

This module demonstrates how to integrate with the dashboard
and send investigation events via WebSocket.
"""

import asyncio
import json
import websockets
from datetime import datetime


async def send_event(ws_url: str, event: dict):
    """Send an event to the dashboard via WebSocket"""
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps(event))
        print(f"Sent event: {event['type']}")


async def demo_full_investigation(ws_url: str = "ws://localhost:8888/ws"):
    """
    Demonstrate a full investigation flow with all event types
    """
    # Create investigation via REST API
    print("Starting demo investigation...")

    # Start investigation via REST
    import requests
    response = requests.post(
        "http://localhost:8888/api/investigations",
        data={
            "incident_description": "Database connection pool exhaustion causing 500 errors",
            "severity": "critical"
        }
    )
    investigation_id = response.json()["id"]
    print(f"Created investigation: {investigation_id}")

    # Give time for the event to propagate
    await asyncio.sleep(1)

    # Simulate agent delegations
    await send_event(ws_url, {
        "type": "agent_delegated",
        "investigation_id": investigation_id,
        "agent": "log_analyst",
        "task": "Analyze database connection logs"
    })
    await asyncio.sleep(1)

    # Agent calls tools
    await send_event(ws_url, {
        "type": "tool_called",
        "investigation_id": investigation_id,
        "agent": "log_analyst",
        "tool_name": "query_logs",
        "tool_input": "SELECT * FROM connection_pool WHERE status='exhausted' LIMIT 100"
    })
    await asyncio.sleep(1)

    # Add findings
    await send_event(ws_url, {
        "type": "finding_added",
        "investigation_id": investigation_id,
        "source_agent": "log_analyst",
        "severity": "critical",
        "description": "Connection pool exhausted at 14:32:15 UTC, 50/50 connections in use"
    })
    await asyncio.sleep(1)

    # Delegate to deployment correlator
    await send_event(ws_url, {
        "type": "agent_delegated",
        "investigation_id": investigation_id,
        "agent": "deployment_correlator",
        "task": "Correlate with recent deployments"
    })
    await asyncio.sleep(1)

    # Deployment correlator finds something
    await send_event(ws_url, {
        "type": "tool_called",
        "investigation_id": investigation_id,
        "agent": "deployment_correlator",
        "tool_name": "get_deployments",
        "tool_input": "app-service 2h"
    })
    await asyncio.sleep(1)

    await send_event(ws_url, {
        "type": "finding_added",
        "investigation_id": investigation_id,
        "source_agent": "deployment_correlator",
        "severity": "high",
        "description": "New version deployed 30 minutes before incident - contains connection pooling change"
    })
    await asyncio.sleep(1)

    # Phase change to investigation
    await send_event(ws_url, {
        "type": "phase_changed",
        "investigation_id": investigation_id,
        "phase": 2
    })
    await asyncio.sleep(1)

    # Delegate to runbook retriever
    await send_event(ws_url, {
        "type": "agent_delegated",
        "investigation_id": investigation_id,
        "agent": "runbook_retriever",
        "task": "Find relevant runbooks and recovery procedures"
    })
    await asyncio.sleep(1)

    await send_event(ws_url, {
        "type": "tool_called",
        "investigation_id": investigation_id,
        "agent": "runbook_retriever",
        "tool_name": "search_runbooks",
        "tool_input": "database connection pool recovery"
    })
    await asyncio.sleep(1)

    await send_event(ws_url, {
        "type": "finding_added",
        "investigation_id": investigation_id,
        "source_agent": "runbook_retriever",
        "severity": "medium",
        "description": "Runbook DB-POOL-RECOVERY-001 found - recommends connection pool reset"
    })
    await asyncio.sleep(1)

    # Phase change to synthesis
    await send_event(ws_url, {
        "type": "phase_changed",
        "investigation_id": investigation_id,
        "phase": 3
    })
    await asyncio.sleep(2)

    # Delegate to communication drafter
    await send_event(ws_url, {
        "type": "agent_delegated",
        "investigation_id": investigation_id,
        "agent": "communication_drafter",
        "task": "Draft incident summary and RCA"
    })
    await asyncio.sleep(1)

    # Phase change to remediation
    await send_event(ws_url, {
        "type": "phase_changed",
        "investigation_id": investigation_id,
        "phase": 4
    })
    await asyncio.sleep(2)

    # Complete investigation
    await send_event(ws_url, {
        "type": "investigation_completed",
        "investigation_id": investigation_id,
        "status": "completed",
        "rca_summary": """## Root Cause Analysis: Database Connection Pool Exhaustion

### Timeline
- 14:32:15 UTC: Service deployment (v2.1.0) with connection pooling changes
- 14:32:45 UTC: First 500 errors reported
- 14:33:20 UTC: Connection pool exhaustion confirmed

### Root Cause
The new connection pooling implementation in v2.1.0 introduced a connection leak.
Each request was creating 2 connections instead of 1, causing the pool to exhaust
within 15 minutes under normal load.

### Contributing Factors
1. Insufficient pre-deployment load testing
2. No automated alerts for connection pool usage
3. Default pool size (50) too low for actual request patterns

### Resolution
1. Rolled back to v2.0.9 - service recovered in 30 seconds
2. Existing connections were naturally drained
3. No data loss occurred

### Recommendations
1. Implement connection pool monitoring with alerts at 80% capacity
2. Add pre-deployment load testing to CI/CD pipeline
3. Increase default connection pool size to 100
4. Implement automatic pool health checks every 30 seconds

### Prevention
- Code review for all connection pooling changes
- Load test new versions against 2x normal peak load
- Canary deployment strategy (5% → 25% → 100%)
"""
    })
    await asyncio.sleep(1)

    print("Demo investigation completed!")


if __name__ == "__main__":
    asyncio.run(demo_full_investigation())
