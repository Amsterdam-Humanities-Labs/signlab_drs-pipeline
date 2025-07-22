# WebSocket Listener Scripts

Two Python scripts are provided to listen to WebSocket connections at localhost:8081 and log all output to both terminal and log file.

## Scripts Available

### 1. websocket_listener_simple.py (RECOMMENDED)
Uses the `websocket-client` library which is already available on your system.

### 2. websocket_listener.py 
Uses the `websockets` library (async version) - requires installation.

## Usage

### Run the Simple Version (Recommended)
```bash
python3 websocket_listener_simple.py
```

### Run the Async Version (if websockets library is installed)
```bash
python3 websocket_listener.py
```

## Installation (if needed)

If you want to use the async version:
```bash
pip3 install websockets
```

If websocket-client is not available:
```bash
pip3 install websocket-client
```

## Features

- **Automatic Reconnection**: Both scripts will automatically reconnect if the connection is lost
- **Dual Logging**: All messages are logged to both:
  - Terminal/console output
  - `websocket_listener.log` file in the same directory
- **JSON Pretty Printing**: JSON messages are automatically formatted for better readability
- **Timestamps**: All log entries include precise timestamps
- **Graceful Shutdown**: Use Ctrl+C to stop the listener cleanly

## Log File

The log file `websocket_listener.log` will be created in the same directory as the script and will contain all WebSocket messages with timestamps.

## Example Output

```
2024-06-27 10:30:15.123 - INFO - Starting WebSocket Listener for ws://localhost:8081
2024-06-27 10:30:15.124 - INFO - Press Ctrl+C to stop
2024-06-27 10:30:15.125 - INFO - Attempting to connect to ws://localhost:8081
2024-06-27 10:30:15.130 - INFO - WebSocket connection established
2024-06-27 10:30:20.456 - INFO - [2024-06-27 10:30:20.456] Received JSON message:
{
  "cameraId": "Camera A",
  "handle": "completed",
  "file": "A20240627_1234.MP4"
}
```