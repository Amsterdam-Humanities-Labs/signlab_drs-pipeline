# Camera Download Controller

A Python script that automates the camera video download process, following the exact workflow from the web interface.

## Prerequisites

1. **RemoteCli** must be installed and accessible
2. **Node.js server** must be running on port 8081 (WebSocket)
3. **Camera server** must be running on port 18080 (HTTP API)
4. **Python 3.7+** with required packages:
   ```bash
   pip3 install websockets requests
   ```

## Quick Start

```bash
python3 camera_download_controller.py
```

## Using Option 1: Complete Download Sequence

Option 1 runs the complete automated download workflow. This is the recommended option for downloading videos from all connected cameras.

### What It Does

1. **Resets state** (for subsequent runs)
2. **Ensures cameras are in remote mode** (state=0)
3. **Runs camera reconnection startup** to get all cameras online
4. **Switches to download mode** and disconnects cameras
5. **Monitors automatic reconnection** in content mode (state=1)
6. **Triggers downloads automatically** when cameras are ready
7. **Tracks download progress** until all cameras complete

### Step-by-Step Usage

1. **Start the script**:
   ```bash
   python3 camera_download_controller.py
   ```

2. **Select Option 1** from the menu:
   ```
   ============================================================
   📷 CAMERA DOWNLOAD CONTROLLER
   ============================================================
   1. Run complete sequence (reconnect startup + download)
   2. Run camera reconnect startup only
   3. Run download sequence only (cameras must be ready)
   4. Check camera status
   5. Monitor WebSocket messages
   6. Exit
   ============================================================
   Select option (1-6): 1
   ```

3. **Monitor the progress**:
   - The script will show each step as it executes
   - Watch for camera status updates and download progress
   - Downloads complete automatically when all cameras finish

4. **Handle errors**:
   - If RemoteCli times out, the script will kill it automatically
   - Follow on-screen instructions to restart RemoteCli manually
   - Press Enter when RemoteCli is running again

### Expected Output

```
🚀 Starting first run of complete sequence...
🔍 Checking camera modes before starting sequence...
✅ All cameras already in correct mode

🔄 Starting camera reconnection startup sequence...

--- Iteration 1/10 ---
🔍 Checking camera status...
Found 5 cameras
  📷 Camera L: status=True, state=0
    ✅ Camera L is ready
  📷 Camera M: status=True, state=0
    ✅ Camera M is ready
[...]
🎉 All 5 cameras are online and ready!

🚀 Starting download sequence...
📊 Expecting downloads from 5 cameras
📡 Setting download mode to 'multiple'...
🔌 Disconnecting all cameras...
✅ Camera disconnect command sent
⏳ Cameras will automatically reconnect in content mode via WebSocket...
📥 Downloads will be triggered automatically when contentsChanged events are received

📊 Monitoring download progress for 5 cameras...
Press Ctrl+C to stop monitoring

📷 Camera L disconnected
  🔄 Auto-reconnecting Camera L in content mode...
[...]
📁 Camera L contents changed - ready for download
  🚀 Auto-triggering download for Camera L...
⬇️  Downloaded: L_001_TakeID.MP4 from Camera L
[...]
✅ Camera L download completed

🎉 All 5 camera downloads completed!

📱 Returning to main menu...
```

### Troubleshooting

#### RemoteCli Crashes
If RemoteCli crashes during operation:
1. The script will detect the timeout (3 seconds)
2. Automatically kill the RemoteCli process
3. Display instructions for manual restart
4. Wait for you to confirm RemoteCli is running

#### Device Property Failed Errors
Multiple `device_prop_failed` messages indicate cameras are being accessed too quickly. The script now includes:
- 3-second delays between camera operations
- 5-second waits between retry cycles
- Random 1-3 second delays for download triggers

#### Connection Errors During Download
If you see `Connection error for ophalen_movie_multiple`:
- The script will automatically retry after 5 seconds
- If retry fails, RemoteCli may need manual restart
- Downloads will resume once RemoteCli is stable

### Running Multiple Times

The script handles multiple runs in the same session:
- Automatically resets all state variables
- Ensures cameras return to remote mode (state=0)
- Clears message queues and tracking data
- Shows run number for clarity

### Best Practices

1. **Wait for cameras to stabilize** before starting
2. **Don't interrupt during camera reconnection** phase
3. **Let downloads complete naturally** (avoid Ctrl+C unless necessary)
4. **Check camera status (Option 4)** if unsure about camera state
5. **Monitor WebSocket messages (Option 5)** for debugging

### Technical Details

The script implements the exact workflow from `opnameViewtest.html`:
- Uses WebSocket for real-time camera events
- Makes HTTP requests to camera API endpoints
- Handles automatic state transitions
- Implements proper timing delays
- Includes error recovery mechanisms

### Error Recovery

The script includes several recovery mechanisms:
- **Automatic RemoteCli restart** on timeout
- **Retry logic** for failed downloads
- **State reset** between runs
- **Camera mode cleanup** before starting
- **Graceful WebSocket handling**

## Other Menu Options

- **Option 2**: Camera reconnect startup only (useful for getting cameras online)
- **Option 3**: Download sequence only (if cameras already in state=0)
- **Option 4**: Check current camera status
- **Option 5**: Monitor raw WebSocket messages
- **Option 6**: Exit the program

## Programmatic Usage

You can also use the controller programmatically in your own scripts:

```python
import asyncio
from camera_download_controller import CameraDownloadController

async def download_all_cameras():
    controller = CameraDownloadController()
    
    # Connect to WebSocket
    if not await controller.connect_websocket():
        print("Failed to connect to WebSocket")
        return False
    
    try:
        # Run the complete download sequence
        success = await controller.run_complete_download_sequence()
        return success
    finally:
        # Clean up
        if controller.websocket:
            await controller.websocket.close()

# Run the download
if __name__ == "__main__":
    result = asyncio.run(download_all_cameras())
    print(f"Download {'succeeded' if result else 'failed'}")
```

The `run_complete_download_sequence()` method handles all the steps:
1. State reset
2. Camera mode verification
3. Camera reconnection startup
4. Download initiation
5. Progress monitoring
6. Automatic cleanup

## Requirements Summary

- Python 3.7+ with `websockets` and `requests` packages
- RemoteCli running (can be started from Desktop)
- Node.js WebSocket server on port 8081
- Camera HTTP API server on port 18080
- Cameras connected and powered on