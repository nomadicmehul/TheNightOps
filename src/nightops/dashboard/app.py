"""
TheNightOps Real-time Investigation Dashboard
FastAPI + WebSocket + Jinja2 application for monitoring agent investigations
"""
import json
import asyncio
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Set
from dataclasses import dataclass, field, asdict
from enum import Enum
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from jinja2 import Environment, FileSystemLoader
import uvicorn
from pathlib import Path


class InvestigationStatus(str, Enum):
    """Investigation status enumeration"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class EventType(str, Enum):
    """WebSocket event types"""
    INVESTIGATION_STARTED = "investigation_started"
    AGENT_DELEGATED = "agent_delegated"
    TOOL_CALLED = "tool_called"
    FINDING_ADDED = "finding_added"
    PHASE_CHANGED = "phase_changed"
    INVESTIGATION_COMPLETED = "investigation_completed"


class SeverityLevel(str, Enum):
    """Severity levels for investigations"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class TimelineEvent:
    """Event in the investigation timeline"""
    timestamp: str
    agent: str
    event_type: str
    description: str
    phase: Optional[int] = None
    tool_name: Optional[str] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class Finding:
    """Investigation finding"""
    id: str
    severity: str
    source_agent: str
    description: str
    timestamp: str

    def to_dict(self):
        return asdict(self)


@dataclass
class AgentAction:
    """Record of what an agent did"""
    agent_name: str
    action: str
    timestamp: str
    result: Optional[str] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class Investigation:
    """Investigation record"""
    id: str
    incident_description: str
    status: InvestigationStatus
    severity: SeverityLevel
    started_at: str
    completed_at: Optional[str] = None
    current_phase: int = 1
    timeline: List[TimelineEvent] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    agent_actions: List[AgentAction] = field(default_factory=list)
    rca_summary: Optional[str] = None

    def to_dict(self):
        return {
            "id": self.id,
            "incident_description": self.incident_description,
            "status": self.status.value,
            "severity": self.severity.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "current_phase": self.current_phase,
            "timeline": [e.to_dict() for e in self.timeline],
            "findings": [f.to_dict() for f in self.findings],
            "agent_actions": [a.to_dict() for a in self.agent_actions],
            "rca_summary": self.rca_summary,
        }


class InvestigationStore:
    """In-memory store for investigations"""

    def __init__(self):
        self.investigations: Dict[str, Investigation] = {}

    def create_investigation(
        self,
        incident_description: str,
        severity: str = "medium"
    ) -> Investigation:
        """Create a new investigation"""
        investigation = Investigation(
            id=str(uuid.uuid4()),
            incident_description=incident_description,
            status=InvestigationStatus.PENDING,
            severity=SeverityLevel(severity),
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self.investigations[investigation.id] = investigation
        return investigation

    def get_investigation(self, investigation_id: str) -> Optional[Investigation]:
        """Get investigation by ID"""
        return self.investigations.get(investigation_id)

    def list_investigations(self) -> List[Investigation]:
        """List all investigations sorted by start time"""
        return sorted(
            self.investigations.values(),
            key=lambda x: x.started_at,
            reverse=True
        )

    def update_status(
        self,
        investigation_id: str,
        status: InvestigationStatus
    ) -> Optional[Investigation]:
        """Update investigation status"""
        investigation = self.get_investigation(investigation_id)
        if investigation:
            investigation.status = status
            if status == InvestigationStatus.COMPLETED:
                investigation.completed_at = datetime.now(timezone.utc).isoformat()
        return investigation

    def add_timeline_event(
        self,
        investigation_id: str,
        agent: str,
        event_type: str,
        description: str,
        phase: Optional[int] = None,
        tool_name: Optional[str] = None,
    ) -> Optional[TimelineEvent]:
        """Add event to investigation timeline"""
        investigation = self.get_investigation(investigation_id)
        if investigation:
            event = TimelineEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                agent=agent,
                event_type=event_type,
                description=description,
                phase=phase,
                tool_name=tool_name,
            )
            investigation.timeline.append(event)
            return event
        return None

    def add_finding(
        self,
        investigation_id: str,
        severity: str,
        source_agent: str,
        description: str,
    ) -> Optional[Finding]:
        """Add a finding to the investigation"""
        investigation = self.get_investigation(investigation_id)
        if investigation:
            finding = Finding(
                id=str(uuid.uuid4()),
                severity=severity,
                source_agent=source_agent,
                description=description,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            investigation.findings.append(finding)
            return finding
        return None

    def add_agent_action(
        self,
        investigation_id: str,
        agent_name: str,
        action: str,
        result: Optional[str] = None,
    ) -> Optional[AgentAction]:
        """Record an agent action"""
        investigation = self.get_investigation(investigation_id)
        if investigation:
            agent_action = AgentAction(
                agent_name=agent_name,
                action=action,
                timestamp=datetime.now(timezone.utc).isoformat(),
                result=result,
            )
            investigation.agent_actions.append(agent_action)
            return agent_action
        return None

    def update_phase(
        self,
        investigation_id: str,
        phase: int
    ) -> Optional[Investigation]:
        """Update investigation phase"""
        investigation = self.get_investigation(investigation_id)
        if investigation:
            investigation.current_phase = phase
        return investigation

    def set_rca_summary(
        self,
        investigation_id: str,
        summary: str
    ) -> Optional[Investigation]:
        """Set the RCA summary"""
        investigation = self.get_investigation(investigation_id)
        if investigation:
            investigation.rca_summary = summary
        return investigation


class ConnectionManager:
    """Manage WebSocket connections and broadcasting"""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        """Register a new WebSocket connection"""
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        """Remove a WebSocket connection"""
        self.active_connections.discard(websocket)

    async def broadcast(self, event_data: Dict[str, Any]):
        """Broadcast event to all connected clients"""
        if not self.active_connections:
            return

        message = json.dumps(event_data)
        disconnected = set()

        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                disconnected.add(connection)

        for connection in disconnected:
            self.disconnect(connection)


def create_app(port: int = 8888) -> FastAPI:
    """Create and configure FastAPI application"""
    app = FastAPI(title="TheNightOps Investigation Dashboard")

    # Store port as app state
    app.port = port

    # Initialize managers
    app.investigation_store = InvestigationStore()
    app.connection_manager = ConnectionManager()

    # CORS middleware — restrict to localhost origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:8888",
            "http://127.0.0.1:8888",
            f"http://localhost:{port}",
            f"http://127.0.0.1:{port}",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Setup Jinja2 templates
    base_dir = Path(__file__).parent
    template_dir = base_dir / "templates"
    jinja_env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=True,
        auto_reload=True,
    )

    # Mount static files
    static_dir = base_dir / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ==================== REST Endpoints ====================

    @app.get("/", response_class=HTMLResponse)
    async def get_dashboard():
        """Serve dashboard page"""
        template = jinja_env.get_template("index.html")
        return template.render()

    @app.get("/api/investigations")
    async def list_investigations():
        """List all investigations"""
        investigations = app.investigation_store.list_investigations()
        return {
            "investigations": [inv.to_dict() for inv in investigations],
            "count": len(investigations),
        }

    @app.get("/api/investigations/{investigation_id}")
    async def get_investigation_detail(investigation_id: str):
        """Get investigation details"""
        investigation = app.investigation_store.get_investigation(investigation_id)
        if not investigation:
            raise HTTPException(status_code=404, detail="Investigation not found")
        return investigation.to_dict()

    @app.post("/api/investigations")
    async def start_investigation(
        incident_description: str,
        severity: str = "medium"
    ):
        """Start a new investigation"""
        investigation = app.investigation_store.create_investigation(
            incident_description=incident_description,
            severity=severity,
        )

        # Update status to in_progress
        app.investigation_store.update_status(
            investigation.id,
            InvestigationStatus.IN_PROGRESS
        )

        # Broadcast event
        await app.connection_manager.broadcast({
            "type": EventType.INVESTIGATION_STARTED.value,
            "investigation_id": investigation.id,
            "incident_description": incident_description,
            "severity": severity,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return investigation.to_dict()

    @app.post("/api/events")
    async def receive_event(event: Dict[str, Any]):
        """Receive investigation events from the agent and broadcast to dashboard clients.

        This is the REST bridge that allows the agent container to push
        real-time events to all connected WebSocket dashboard viewers.
        """
        investigation_id = event.get("investigation_id")
        event_type = event.get("type", "")
        event["timestamp"] = datetime.now(timezone.utc).isoformat()

        if investigation_id:
            if event_type == EventType.TOOL_CALLED.value:
                app.investigation_store.add_timeline_event(
                    investigation_id,
                    event.get("agent", "nightops"),
                    event_type,
                    f"Called {event.get('tool_name', '?')}: {event.get('tool_input', '')[:200]}",
                    tool_name=event.get("tool_name"),
                )
            elif event_type == EventType.FINDING_ADDED.value:
                app.investigation_store.add_finding(
                    investigation_id,
                    event.get("severity", "medium"),
                    event.get("source_agent", "nightops"),
                    event.get("description", ""),
                )
                app.investigation_store.add_timeline_event(
                    investigation_id,
                    event.get("source_agent", "nightops"),
                    event_type,
                    event.get("description", ""),
                )
            elif event_type == EventType.AGENT_DELEGATED.value:
                app.investigation_store.add_timeline_event(
                    investigation_id,
                    event.get("agent", "nightops"),
                    event_type,
                    event.get("task", "")[:300],
                )
                app.investigation_store.add_agent_action(
                    investigation_id,
                    event.get("agent", "nightops"),
                    event.get("task", "")[:300],
                )
            elif event_type == EventType.PHASE_CHANGED.value:
                phase = event.get("phase")
                if phase is not None:
                    app.investigation_store.update_phase(investigation_id, phase)
                    app.investigation_store.add_timeline_event(
                        investigation_id,
                        "system",
                        event_type,
                        f"Progressed to Phase {phase}",
                        phase=phase,
                    )
            elif event_type == EventType.INVESTIGATION_COMPLETED.value:
                status = event.get("status", "completed")
                rca_summary = event.get("rca_summary", "")
                # Mark phase 4 as complete
                app.investigation_store.update_phase(investigation_id, 4)
                app.investigation_store.update_status(
                    investigation_id,
                    InvestigationStatus(status),
                )
                if rca_summary:
                    app.investigation_store.set_rca_summary(
                        investigation_id, rca_summary,
                    )
                app.investigation_store.add_timeline_event(
                    investigation_id,
                    "system",
                    event_type,
                    f"Investigation {status}",
                )

        # Broadcast to all WebSocket clients
        await app.connection_manager.broadcast(event)
        return {"status": "ok"}

    # ==================== WebSocket Endpoint ====================

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for real-time events"""
        await app.connection_manager.connect(websocket)
        try:
            while True:
                # Keep connection alive and listen for any client messages
                # (for future two-way communication)
                data = await websocket.receive_text()
                event = json.loads(data)

                # Handle different event types from client
                event_type = event.get("type")
                investigation_id = event.get("investigation_id")

                if event_type == EventType.AGENT_DELEGATED.value:
                    agent = event.get("agent")
                    task = event.get("task")
                    app.investigation_store.add_timeline_event(
                        investigation_id,
                        agent,
                        event_type,
                        task,
                    )
                    app.investigation_store.add_agent_action(
                        investigation_id,
                        agent,
                        task,
                    )

                elif event_type == EventType.TOOL_CALLED.value:
                    agent = event.get("agent")
                    tool_name = event.get("tool_name")
                    tool_input = event.get("tool_input")
                    app.investigation_store.add_timeline_event(
                        investigation_id,
                        agent,
                        event_type,
                        f"Called {tool_name}: {tool_input}",
                        tool_name=tool_name,
                    )

                elif event_type == EventType.FINDING_ADDED.value:
                    severity = event.get("severity", "medium")
                    source_agent = event.get("source_agent")
                    description = event.get("description")
                    finding = app.investigation_store.add_finding(
                        investigation_id,
                        severity,
                        source_agent,
                        description,
                    )
                    app.investigation_store.add_timeline_event(
                        investigation_id,
                        source_agent,
                        event_type,
                        f"Found: {description}",
                    )
                    event["finding_id"] = finding.id if finding else None

                elif event_type == EventType.PHASE_CHANGED.value:
                    phase = event.get("phase")
                    app.investigation_store.update_phase(investigation_id, phase)
                    app.investigation_store.add_timeline_event(
                        investigation_id,
                        "system",
                        event_type,
                        f"Progressed to Phase {phase}",
                        phase=phase,
                    )

                elif event_type == EventType.INVESTIGATION_COMPLETED.value:
                    rca_summary = event.get("rca_summary", "")
                    status = event.get("status", "completed")
                    app.investigation_store.update_status(
                        investigation_id,
                        InvestigationStatus(status),
                    )
                    if rca_summary:
                        app.investigation_store.set_rca_summary(
                            investigation_id,
                            rca_summary,
                        )
                    app.investigation_store.add_timeline_event(
                        investigation_id,
                        "system",
                        event_type,
                        f"Investigation {status}",
                    )

                # Broadcast event to all other clients
                event["timestamp"] = datetime.now(timezone.utc).isoformat()
                await app.connection_manager.broadcast(event)

        except WebSocketDisconnect:
            app.connection_manager.disconnect(websocket)

    return app


if __name__ == "__main__":
    app = create_app(port=8888)
    uvicorn.run(app, host="0.0.0.0", port=8888)
