# Enhanced startMonitor.py Development Plan

## Project Overview
Enhance the existing PyQt5-based Sony camera monitoring application with comprehensive studio system monitoring including Node.js server status and file management dashboard.

## Current State Analysis
- **Existing Features**: Camera USB monitoring, RemoteCli process management, troubleshooting GUI
- **Architecture**: PyQt5 with multi-threading, WebSocket integration ready
- **Target Enhancements**: Node.js server monitoring, file status API integration, media formatting controls

## 1. Node.js Server Status Monitoring

### Requirements
- Monitor `startServer_beta.js` process running on port 8080
- Real-time status display with visual indicators
- Integration with existing RemoteCli status panel

### Implementation
```python
def check_nodejs_server_status(self):
    """Check if Node.js server is running on port 8080"""
    try:
        # Method 1: Process detection
        result = subprocess.run(
            ["pgrep", "-f", "startServer_beta.js"],
            capture_output=True, text=True
        )
        process_running = result.returncode == 0
        
        # Method 2: Port connectivity test
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        port_accessible = sock.connect_ex(('localhost', 8080)) == 0
        sock.close()
        
        return process_running and port_accessible
    except Exception:
        return False
```

### UI Components
- Status label next to RemoteCli status
- Color coding: Green (Running), Red (Not Running), Orange (Partial)
- Auto-refresh every 5 seconds

## 2. File Status API Integration

### API Specification
- **Endpoint**: `https://signcollect.nl/listfiles.json`
- **Method**: HTTP GET
- **Response Format**:
```json
{
    "timestamp": "2025-06-13T16:43:44.903038",
    "base_directory": "/path/to/studioFiles",
    "folders": [
        {
            "date": "2025-06-10",
            "status": "ok",
            "files": {"A": 294, "B": 293, "L": 294, "M": 293, "R": 293},
            "total": 1467,
            "is_ok": false
        }
    ]
}
```

### Implementation
```python
def fetch_file_status(self):
    """Fetch file status from API"""
    try:
        response = requests.get(
            'https://signcollect.nl/listfiles.json',
            timeout=10,
            headers={'Accept': 'application/json'}
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        self.log_message(f"Error fetching file status: {e}", 'red')
        return None
```

### Data Processing
- Extract last 5 date entries from `folders` array
- Calculate format eligibility: all 5 dates must have `is_ok: true`
- Track file count discrepancies per camera

### UI Components
```python
def create_file_status_table(self):
    """Create table showing file status by date"""
    self.file_status_table = QTableWidget()
    self.file_status_table.setColumnCount(8)  # Date, A, B, L, M, R, Total, Status
    self.file_status_table.setHorizontalHeaderLabels([
        "Date", "Camera A", "Camera B", "Camera L", "Camera M", "Camera R", "Total", "Status"
    ])
    self.file_status_table.setEditTriggers(QTableWidget.NoEditTriggers)
    return self.file_status_table
```

## 3. Media Formatting Feature

### WebSocket Integration
- **Server**: `ws://localhost:8081` (from startServer_beta.js:24)
- **Purpose**: Listen for `formatCompleted` messages
- **Connection Management**: Auto-reconnect on disconnect

### Format Trigger
- **Endpoint**: `http://localhost:8080/format_media_all`
- **Method**: HTTP GET
- **Prerequisite**: Last 5 dates must have `is_ok: true`

### Implementation
```python
def connect_websocket(self):
    """Connect to Node.js WebSocket server"""
    try:
        self.ws = websocket.WebSocketApp(
            "ws://localhost:8081",
            on_message=self.on_websocket_message,
            on_error=self.on_websocket_error,
            on_close=self.on_websocket_close
        )
        # Run in separate thread
        self.ws_thread = threading.Thread(target=self.ws.run_forever, daemon=True)
        self.ws_thread.start()
    except Exception as e:
        self.log_message(f"WebSocket connection failed: {e}", 'red')

def trigger_format_media(self):
    """Trigger media formatting via HTTP GET"""
    if not self.can_format():
        QMessageBox.warning(
            self, "Format Warning", 
            "Cannot format: Not all recent dates have is_ok=true status"
        )
        return
    
    # Safety warning
    reply = QMessageBox.question(
        self, "Format Media Warning",
        "⚠️ WARNING: Media formatting can only be done BEFORE recording!\n\n"
        "This will format all camera media. Are you sure you want to continue?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No
    )
    
    if reply == QMessageBox.Yes:
        self.format_button.setEnabled(False)
        self.log_message("Starting media format operation...", 'blue')
        
        try:
            response = requests.get('http://localhost:8080/format_media_all', timeout=30)
            if response.status_code == 200:
                self.log_message("Format request sent successfully", 'green')
            else:
                self.log_message(f"Format request failed: {response.status_code}", 'red')
        except Exception as e:
            self.log_message(f"Format request error: {e}", 'red')
        finally:
            self.format_button.setEnabled(True)
```

### WebSocket Message Handling
```python
def on_websocket_message(self, ws, message):
    """Handle WebSocket messages from Node.js server"""
    try:
        data = json.loads(message)
        if data.get('handle') == 'formatCompleted':
            camera_id = data.get('cameraId', 'Unknown')
            format_msg = data.get('formatMessage', 'Completed')
            self.log_message(f"Format completed for {camera_id}: {format_msg}", 'green')
            
            # Re-enable format button
            self.format_button.setEnabled(True)
    except json.JSONDecodeError:
        self.log_message(f"Invalid WebSocket message: {message}", 'orange')
```

## 4. Enhanced UI Layout

### New Layout Structure
```
┌─────────────────────────────────────────────────────────────┐
│ HELP! Handleiding hier...                                   │
├─────────────────────────────────────────────────────────────┤
│ Sony Camera Status                                          │
│ [Existing Camera Table]                                     │
├─────────────────────────────────────────────────────────────┤
│ System Status                                               │
│ RemoteCli Status: [Running] | Node.js Server: [Running]    │
├─────────────────────────────────────────────────────────────┤
│ File Status Dashboard                                       │
│ [File Status Table - Last 5 Dates]                         │
│ Format Eligible: [Yes/No] [FORMAT MEDIA] [Refresh Status]  │
├─────────────────────────────────────────────────────────────┤
│ Console Output                                              │
│ [Existing Console + New Logging]                           │
├─────────────────────────────────────────────────────────────┤
│ [Restart RemoteCli] [Force Restart RemoteCli]              │
└─────────────────────────────────────────────────────────────┘
```

### New UI Components
```python
def create_system_status_panel(self):
    """Create combined system status panel"""
    status_layout = QHBoxLayout()
    
    # RemoteCli Status (existing)
    remotecli_label = QLabel("RemoteCli Status:")
    self.remote_cli_status = QLabel("Unknown")
    
    # Node.js Server Status (new)
    nodejs_label = QLabel("Node.js Server:")
    self.nodejs_status = QLabel("Unknown")
    
    status_layout.addWidget(remotecli_label)
    status_layout.addWidget(self.remote_cli_status)
    status_layout.addWidget(QLabel(" | "))
    status_layout.addWidget(nodejs_label)
    status_layout.addWidget(self.nodejs_status)
    status_layout.addStretch()
    
    return status_layout

def create_format_control_panel(self):
    """Create media formatting control panel"""
    format_layout = QHBoxLayout()
    
    self.format_eligible_label = QLabel("Format Eligible: Checking...")
    self.format_button = QPushButton("FORMAT MEDIA")
    self.format_button.setEnabled(False)
    self.refresh_status_button = QPushButton("Refresh Status")
    
    # Style format button prominently
    self.format_button.setStyleSheet("""
        QPushButton {
            background-color: #ff6b35;
            color: white;
            font-weight: bold;
            padding: 12px;
            border: 2px solid #e55100;
        }
        QPushButton:hover {
            background-color: #ff8a50;
        }
        QPushButton:disabled {
            background-color: #cccccc;
            color: #666666;
        }
    """)
    
    format_layout.addWidget(self.format_eligible_label)
    format_layout.addWidget(self.format_button)
    format_layout.addWidget(self.refresh_status_button)
    format_layout.addStretch()
    
    return format_layout
```

## 5. Data Refresh Strategy

### Timer Configuration
```python
def init_enhanced_timers(self):
    """Initialize all monitoring timers"""
    # Existing USB timer (10 seconds)
    self.usb_timer = QTimer()
    self.usb_timer.timeout.connect(self.update_table)
    self.usb_timer.start(10000)
    
    # Enhanced system status timer (5 seconds)
    self.system_status_timer = QTimer()
    self.system_status_timer.timeout.connect(self.update_system_status)
    self.system_status_timer.start(5000)
    
    # File status timer (30 seconds)
    self.file_status_timer = QTimer()
    self.file_status_timer.timeout.connect(self.update_file_status)
    self.file_status_timer.start(30000)
    
    # Initial updates
    self.update_system_status()
    self.update_file_status()
```

## 6. Error Handling Strategy

### Network Error Management
```python
def handle_network_error(self, operation, error):
    """Centralized network error handling"""
    error_messages = {
        'timeout': 'Connection timeout - check network connectivity',
        'connection': 'Cannot connect to server - check if service is running',
        'http_error': 'Server returned an error - check server status',
        'json_error': 'Invalid response format - server may be malfunctioning'
    }
    
    error_type = self.classify_error(error)
    message = error_messages.get(error_type, f"Unknown error: {error}")
    
    self.log_message(f"{operation} failed: {message}", 'red')
    
    # Update UI to reflect error state
    if operation == 'file_status':
        self.format_button.setEnabled(False)
        self.format_eligible_label.setText("Format Eligible: Error - Cannot determine")
```

## 7. Dependencies and Installation

### Required Python Packages
```bash
pip install requests websocket-client
```

### Import Additions
```python
import requests
import websocket
import json
import socket
from datetime import datetime
```

## 8. Testing Strategy

### Unit Testing
- Mock API responses for offline testing
- Test WebSocket message handling
- Validate UI state changes

### Integration Testing
- Test with actual Node.js server
- Verify API connectivity
- Test WebSocket real-time communication

### User Acceptance Testing
- Verify format safety warnings
- Test error handling scenarios
- Validate status indicator accuracy

## 9. Deployment Considerations

### Configuration
- Hardcoded URLs for now (localhost:8080, signcollect.nl)
- Future: Move to configuration file

### Logging
- Enhanced console logging for all operations
- Consider file-based logging for debugging

### Performance
- Efficient polling intervals
- Non-blocking network operations
- Graceful degradation on network failures

## 10. Future Enhancements

### Potential Features
- Historical file status trending
- Advanced formatting options
- Camera-specific formatting controls
- Real-time file count monitoring
- Integration with DaVinci Resolve status

### Scalability
- Configuration-driven server endpoints
- Plugin architecture for additional monitoring
- Multi-studio support

This plan provides a comprehensive roadmap for transforming startMonitor.py into a full studio monitoring dashboard while maintaining existing functionality and adding powerful new capabilities for file management and server monitoring.