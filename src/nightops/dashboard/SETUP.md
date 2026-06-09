# TheNightOps Dashboard - Setup & Usage Guide

## Quick Start

### 1. Install Dependencies

```bash
cd dashboard
pip install -r requirements.txt
```

### 2. Run the Dashboard

```bash
python app.py
```

The dashboard will be available at: **http://localhost:8888**

### 3. Access the Dashboard

Open your browser and navigate to http://localhost:8888. You should see:
- Empty investigations list on the left
- Empty state message on the right
- Green connection indicator in the header

## Demo Mode

Run the included demo to see the dashboard in action:

```bash
# Terminal 1: Start the dashboard
python app.py

# Terminal 2: Run the demo (after dashboard is running)
python examples.py
```

The demo will:
1. Create a new investigation
2. Send a sequence of events (agent delegations, tool calls, findings)
3. Progress through all four phases
4. Complete with a full RCA summary

Watch in real-time as the dashboard updates!

## Integration Guide

### Sending Events from Your Agent System

Your agent orchestrator should send WebSocket events to `ws://localhost:8888/ws`.

#### Python Integration

```python
import asyncio
import json
import websockets

async def send_investigation_event(ws_url: str, event: dict):
    """Send an event to the dashboard"""
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps(event))

# Example: Agent delegated
await send_investigation_event(
    "ws://localhost:8888/ws",
    {
        "type": "agent_delegated",
        "investigation_id": "inv-12345",
        "agent": "log_analyst",
        "task": "Analyze error logs"
    }
)
```

#### JavaScript/TypeScript Integration

```javascript
const ws = new WebSocket('ws://localhost:8888/ws');

// Send when agent is delegated a task
ws.send(JSON.stringify({
    type: 'agent_delegated',
    investigation_id: 'inv-12345',
    agent: 'log_analyst',
    task: 'Analyze error logs'
}));

// Listen for messages (if implementing two-way communication)
ws.onmessage = (event) => {
    const message = JSON.parse(event.data);
    console.log('Received:', message);
};
```

### Event Types & Schemas

#### 1. Investigation Started
**Created via REST API endpoint POST /api/investigations**

```json
{
    "type": "investigation_started",
    "investigation_id": "uuid",
    "incident_description": "Description of the incident",
    "severity": "critical|high|medium|low",
    "timestamp": "ISO8601 timestamp"
}
```

#### 2. Agent Delegated
**When an agent is assigned a task**

```json
{
    "type": "agent_delegated",
    "investigation_id": "uuid",
    "agent": "log_analyst|deployment_correlator|runbook_retriever|communication_drafter|system",
    "task": "Description of the task",
    "timestamp": "ISO8601 timestamp (optional, added by server)"
}
```

#### 3. Tool Called
**When an agent calls a tool**

```json
{
    "type": "tool_called",
    "investigation_id": "uuid",
    "agent": "log_analyst|deployment_correlator|runbook_retriever|communication_drafter",
    "tool_name": "Tool name",
    "tool_input": "Input parameters or query",
    "timestamp": "ISO8601 timestamp (optional)"
}
```

#### 4. Finding Added
**When an agent discovers a finding**

```json
{
    "type": "finding_added",
    "investigation_id": "uuid",
    "source_agent": "log_analyst|deployment_correlator|runbook_retriever|communication_drafter",
    "severity": "critical|high|medium|low",
    "description": "Description of the finding",
    "timestamp": "ISO8601 timestamp (optional)"
}
```

#### 5. Phase Changed
**When investigation progresses to next phase**

```json
{
    "type": "phase_changed",
    "investigation_id": "uuid",
    "phase": 1|2|3|4,
    "timestamp": "ISO8601 timestamp (optional)"
}
```

#### 6. Investigation Completed
**When investigation finishes**

```json
{
    "type": "investigation_completed",
    "investigation_id": "uuid",
    "status": "completed|failed",
    "rca_summary": "Full RCA markdown text (optional)",
    "timestamp": "ISO8601 timestamp (optional)"
}
```

## Configuration

### Change Port

```python
# In app.py, change the port in the last block:
if __name__ == "__main__":
    app = create_app(port=9000)  # Change 8888 to your port
    uvicorn.run(app, host="0.0.0.0", port=9000)
```

Or via environment variable (if you modify app.py):

```python
import os
port = int(os.getenv("DASHBOARD_PORT", 8888))
app = create_app(port=port)
uvicorn.run(app, host="0.0.0.0", port=port)
```

### Change Host

```python
# Allow connections from any host
uvicorn.run(app, host="0.0.0.0", port=8888)

# Only localhost (more secure in development)
uvicorn.run(app, host="127.0.0.1", port=8888)
```

### CORS Configuration

Edit `app.py` and modify the CORS middleware:

```python
# Allow all origins (development)
allow_origins=["*"]

# Allow specific origin (production)
allow_origins=["https://yourdomain.com"]

# Allow multiple origins
allow_origins=["https://yourdomain.com", "https://monitor.yourdomain.com"]
```

## API Reference

### GET `/`
Returns the dashboard HTML page

### GET `/api/investigations`
List all investigations

**Response:**
```json
{
    "investigations": [
        {
            "id": "uuid",
            "incident_description": "...",
            "status": "in_progress",
            "severity": "high",
            "started_at": "2024-03-01T14:32:15.123Z",
            "completed_at": null,
            "current_phase": 2,
            "timeline": [...],
            "findings": [...],
            "agent_actions": [...],
            "rca_summary": null
        }
    ],
    "count": 1
}
```

### GET `/api/investigations/{investigation_id}`
Get details for a specific investigation

**Response:** Single investigation object (see above)

### POST `/api/investigations`
Start a new investigation

**Parameters:**
- `incident_description` (string, required): Description of the incident
- `severity` (string, optional): One of critical, high, medium, low (default: medium)

**Response:** Investigation object with newly generated ID

## Frontend User Guide

### Dashboard Layout

**Header**
- Title: "TheNightOps — Investigation Dashboard"
- Connection status indicator (green = connected, red = disconnected)
- Auto-reconnects if disconnected

**Left Panel (30%)**
- List of all investigations
- Click to select and view details
- Status badges: pending, in_progress, completed, failed
- Severity indicators: critical (red), high (yellow), medium (cyan), low (gray)
- "New" button to create investigation

**Right Panel (70%)**
- Investigation header with description, severity, status
- Duration timer (auto-updating)
- Phase progress bar (4 phases)
- Timeline of events (scrollable)
- Findings grid (with severity colors)
- RCA summary area (filled when investigation completes)

**Footer**
- Connected clients count
- Last event timestamp

### Creating a New Investigation

1. Click the **"+ New"** button
2. Enter incident description
3. Select severity level
4. Click "Start Investigation"
5. Investigation appears in left panel
6. Click to view in main panel

### Monitoring Live Events

The timeline will automatically scroll to show new events as they arrive.
The phase indicator updates in real-time.
New findings pulse briefly to draw attention.

## Troubleshooting

### Dashboard won't load

**Check 1:** Is the server running?
```bash
curl http://localhost:8888/
```

**Check 2:** Is port 8888 in use?
```bash
lsof -i :8888  # macOS/Linux
netstat -ano | findstr :8888  # Windows
```

**Check 3:** Check console for errors
- Open browser developer tools (F12)
- Go to Console tab
- Check for error messages

### WebSocket connection fails

**Check 1:** Server must be running
```bash
python app.py
```

**Check 2:** Firewall allows WebSocket
- WebSocket needs TCP port 8888
- Check firewall rules

**Check 3:** Browser supports WebSocket
- All modern browsers support it
- Check if behind proxy that blocks WebSocket

**Check 4:** Browser console for errors
- F12 → Console → Check for errors

### Events not appearing

**Check 1:** Verify event JSON format
- Use the schema provided above
- Ensure `investigation_id` matches

**Check 2:** Check WebSocket is connected
- Green dot should show in header
- Check browser Network tab for WebSocket connection

**Check 3:** Check server logs
- Look for error messages in the Python console

### Empty investigations list

**Check 1:** Have you created an investigation?
- Click "+ New" button
- Submit the form

**Check 2:** API working?
```bash
curl http://localhost:8888/api/investigations
```

Should return JSON with investigations array.

## Performance Tips

### For Development
- In-memory storage is fine
- Data is lost when server restarts

### For Production
- Consider adding database persistence
- Monitor memory usage for long-running investigations
- Archive old investigations
- Use multiple workers: `uvicorn app:app --workers 4`

## Browser DevTools Tips

### Network Tab
Watch WebSocket messages in real-time:
1. Open DevTools (F12)
2. Go to Network tab
3. Filter by "WS" (WebSocket)
4. Click on the WebSocket connection
5. View messages in the "Messages" sub-tab

### Console
Send test events from console:
```javascript
// Get the WebSocket from the page
const ws = new WebSocket('ws://localhost:8888/ws');
ws.onopen = () => {
    ws.send(JSON.stringify({
        type: 'agent_delegated',
        investigation_id: document.querySelector('.investigation-item').onclick.toString().match(/'([^']+)'/)[1],
        agent: 'log_analyst',
        task: 'Test event'
    }));
};
```

## Next Steps

1. **Integrate with your agent system**
   - Modify agent code to send WebSocket events
   - Test with `examples.py`

2. **Customize styling**
   - Edit colors in `templates/index.html` CSS variables
   - Add your logo
   - Adjust layout

3. **Add persistence**
   - Integrate with PostgreSQL/MongoDB
   - Store investigations in database
   - Query historical data

4. **Deploy**
   - Use gunicorn for production
   - Put behind nginx/reverse proxy
   - Use systemd/docker for management

## Example: Full Integration

Here's a minimal example integrating the dashboard with an agent:

```python
import asyncio
import websockets
import json

class InvestigationOrchestrator:
    def __init__(self, dashboard_ws_url: str):
        self.ws_url = dashboard_ws_url

    async def run_investigation(self, incident: str, severity: str):
        # Create investigation (via REST)
        import requests
        resp = requests.post(
            f"{self.ws_url.replace('ws:', 'http:').split('/ws')[0]}/api/investigations",
            data={"incident_description": incident, "severity": severity}
        )
        inv_id = resp.json()["id"]

        # Delegate to log analyst
        await self._send_event({
            "type": "agent_delegated",
            "investigation_id": inv_id,
            "agent": "log_analyst",
            "task": "Analyze logs"
        })

        # ... more events ...

        # Complete
        await self._send_event({
            "type": "investigation_completed",
            "investigation_id": inv_id,
            "status": "completed",
            "rca_summary": "Found root cause: ..."
        })

    async def _send_event(self, event: dict):
        async with websockets.connect(self.ws_url) as ws:
            await ws.send(json.dumps(event))

# Usage
orchestrator = InvestigationOrchestrator("ws://localhost:8888/ws")
asyncio.run(orchestrator.run_investigation(
    incident="High error rate",
    severity="critical"
))
```

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Review `README.md` for architecture details
3. Check browser console and server logs
4. Review event schemas to ensure proper format

Enjoy using TheNightOps Investigation Dashboard!
