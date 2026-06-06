# TheNightOps Investigation Dashboard

A professional, real-time investigation dashboard for monitoring AI agent investigations using FastAPI, WebSocket, and Jinja2.

## Overview

TheNightOps dashboard provides a conference-demo-ready interface for visualizing agent investigation activity in real-time. It tracks investigation progress across four phases (Triage, Deep Investigation, Synthesis, Remediation) and displays:

- **Live investigation timeline** with agent actions and tool calls
- **Real-time findings** with severity badges
- **Investigation phases** with progress visualization
- **Root Cause Analysis (RCA)** summaries
- **WebSocket-based live updates** with automatic reconnection
- **Responsive dark-themed UI** inspired by GitHub's design system

## Architecture

### Backend (`app.py`)

FastAPI application with the following components:

#### Data Models
- `InvestigationStatus`: Enum for investigation states (pending, in_progress, completed, failed)
- `EventType`: Enum for WebSocket event types
- `SeverityLevel`: Enum for severity levels (critical, high, medium, low)
- `TimelineEvent`: Event record with timestamp, agent, type, and description
- `Finding`: Investigation finding with severity and source agent
- `AgentAction`: Record of what each sub-agent did
- `Investigation`: Complete investigation object with all above data

#### Core Classes

**InvestigationStore**
- In-memory storage for investigations
- Methods for creating, retrieving, and updating investigations
- Methods for adding timeline events, findings, and agent actions
- Methods for updating phase and RCA summary

**ConnectionManager**
- Manages WebSocket connections
- Handles connect/disconnect events
- Broadcasts events to all connected clients
- Auto-cleanup of dead connections

#### REST Endpoints
- `GET /` - Dashboard HTML page
- `GET /api/investigations` - List all investigations
- `GET /api/investigations/{id}` - Get investigation details
- `POST /api/investigations` - Start new investigation

#### WebSocket Endpoint
- `GET /ws` - WebSocket connection for real-time events

**Event Types Handled:**
- `investigation_started`: New investigation created
- `agent_delegated`: Agent assigned a task
- `tool_called`: Agent called a tool
- `finding_added`: Finding discovered during investigation
- `phase_changed`: Investigation moved to next phase
- `investigation_completed`: Investigation finished

### Frontend (`templates/index.html`)

Single-page application with:

#### Layout
- **Header (5%)**: Title and connection status
- **Left Panel (30%)**: Investigations list with filtering
- **Right Panel (65%)**: Investigation details
- **Footer (2%)**: Connection info and timestamps

#### Components
1. **Investigation List**
   - Filterable list of all investigations
   - Status badges (color-coded)
   - Severity indicators
   - Click to select investigation

2. **Investigation Detail**
   - Incident description
   - Severity and status badges
   - Duration timer (auto-updating)
   - Current phase display

3. **Phase Indicator**
   - Visual progress bar: Phase 1 → 2 → 3 → 4
   - Color-coded states (completed, active, pending)
   - Smooth transitions

4. **Live Timeline**
   - Scrolling event list with auto-scroll to bottom
   - Event types: agent delegation, tool calls, findings, phase changes
   - Agent badges (different colors per agent type)
   - Timestamps for each event

5. **Findings Panel**
   - Grid layout of finding cards
   - Severity badges
   - Source agent attribution
   - Pulse animation on new findings

6. **RCA Summary**
   - Markdown-friendly text area
   - Filled when investigation completes

#### Features
- **WebSocket Connection Management**
  - Auto-reconnect with exponential backoff
  - Configurable max reconnection attempts
  - Live connection status indicator

- **Real-time Updates**
  - Parse incoming WebSocket events
  - Update DOM without page refresh
  - Smooth animations for new content
  - Auto-scroll timeline to bottom

- **Modal Dialog**
  - Create new investigation modal
  - Incident description textarea
  - Severity level dropdown
  - Form validation

#### Color Scheme
- **Background**: #0d1117 (GitHub dark)
- **Cards**: #161b22
- **Borders**: #30363d
- **Text**: #c9d1d9
- **Accent**: #58a6ff (cyan)
- **Success**: #3fb950 (green)
- **Warning**: #d29922 (yellow)
- **Error**: #f85149 (red)

### Static Files

**`static/nightops.js`**
- Utility functions for formatting and DOM manipulation
- Optional helper functions (most logic is inline in index.html for simplicity)

## Usage

### Installation

```bash
pip install fastapi uvicorn jinja2
```

### Running the Dashboard

```python
from dashboard.app import create_app
import uvicorn

app = create_app(port=8888)
uvicorn.run(app, host="0.0.0.0", port=8888)
```

Or directly:

```bash
python dashboard/app.py
```

Dashboard will be available at `http://localhost:8888`

### Starting an Investigation

1. Click the **"+ New"** button in the left panel
2. Enter incident description
3. Select severity level (low, medium, high, critical)
4. Click "Start Investigation"

The dashboard will create a new investigation and display it in the list.

### Sending Events via WebSocket

To integrate with your agent system, send events to the WebSocket endpoint:

```javascript
const ws = new WebSocket('ws://localhost:8888/ws');

// Agent delegated
ws.send(JSON.stringify({
    type: 'agent_delegated',
    investigation_id: 'inv-id',
    agent: 'log_analyst',
    task: 'Analyze logs for errors'
}));

// Tool called
ws.send(JSON.stringify({
    type: 'tool_called',
    investigation_id: 'inv-id',
    agent: 'log_analyst',
    tool_name: 'grep_logs',
    tool_input: 'ERROR'
}));

// Finding added
ws.send(JSON.stringify({
    type: 'finding_added',
    investigation_id: 'inv-id',
    agent: 'log_analyst',
    severity: 'high',
    description: 'Database connection timeout at 14:32 UTC'
}));

// Phase changed
ws.send(JSON.stringify({
    type: 'phase_changed',
    investigation_id: 'inv-id',
    phase: 2
}));

// Investigation completed
ws.send(JSON.stringify({
    type: 'investigation_completed',
    investigation_id: 'inv-id',
    status: 'completed',
    rca_summary: 'Database pool exhaustion due to...'
}));
```

## File Structure

```
dashboard/
├── __init__.py                 # Package initialization
├── app.py                      # Main FastAPI application (16KB)
│   ├── InvestigationStatus
│   ├── EventType
│   ├── SeverityLevel
│   ├── TimelineEvent
│   ├── Finding
│   ├── AgentAction
│   ├── Investigation
│   ├── InvestigationStore
│   ├── ConnectionManager
│   └── create_app()
├── templates/
│   ├── index.html             # Main dashboard SPA (45KB)
│   │   ├── HTML structure
│   │   ├── CSS (inline, ~1200 lines)
│   │   └── JavaScript (inline, ~700 lines)
│   └── components.html        # Placeholder for future components
└── static/
    └── nightops.js            # Utility functions (2.5KB)
```

## Key Features

### Conference-Demo Ready
- Professional dark theme
- Smooth animations and transitions
- Responsive layout
- Clear visual hierarchy
- Real-time responsiveness

### Scalable Architecture
- In-memory store can be swapped with database
- WebSocket supports unlimited concurrent connections
- Event-driven architecture for easy extension
- Clean separation of concerns

### Developer-Friendly
- Well-documented code with docstrings
- Clear data model definitions
- RESTful API for programmatic control
- Extensible event system

## Configuration

### Port Configuration
```python
app = create_app(port=9000)  # Change default port
```

### CORS Settings
Edit the CORS middleware in `app.py`:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://yourdomain.com"],  # Restrict origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Static Files
Place additional static files in `static/` directory. They will be served at `/static/`.

## Performance Considerations

- **In-memory storage**: Suitable for development and demos. For production, integrate with a database.
- **WebSocket connections**: FastAPI handles concurrent WebSocket connections efficiently.
- **Timeline events**: Consider archiving old investigations to manage memory.
- **Browser limits**: Dashboard tested with 100+ concurrent events per investigation.

## Customization

### Adding New Event Types
1. Add to `EventType` enum in `app.py`
2. Add handler in `handleWebSocketEvent()` in `index.html`
3. Update Timeline rendering in `renderTimeline()`

### Changing Colors
Update CSS variables in `index.html` `<style>` section:
```css
:root {
    --bg-primary: #0d1117;
    --accent-cyan: #58a6ff;
    /* ... */
}
```

### Adding Agent Types
Update the `timeline-badge` classes in CSS and the agent badge logic in JavaScript.

## Browser Support

- Chrome/Chromium 90+
- Firefox 88+
- Safari 14+
- Edge 90+

WebSocket support is required.

## Troubleshooting

### WebSocket Connection Issues
1. Check browser console for errors
2. Verify server is running: `curl http://localhost:8888/`
3. Check CORS settings if connecting from different origin
4. Ensure firewall allows WebSocket connections

### Investigations Not Appearing
1. Check network tab for API requests
2. Verify `POST /api/investigations` returns 200
3. Check browser console for JavaScript errors

### Events Not Updating
1. Check WebSocket is connected (green dot in header)
2. Verify event JSON is properly formatted
3. Check server logs for WebSocket errors

## Future Enhancements

- Database persistence (PostgreSQL/MongoDB)
- Authentication and authorization
- Investigation filtering and search
- Export/download investigation reports
- Webhook notifications for investigation events
- Performance metrics dashboard
- Investigation templates
- Team collaboration features

## License

Part of TheNightOps project.
