# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Commands

### Start All Services
```bash
python startupScript.py
```
This starts the complete video processing pipeline including the Node.js server, monitoring services, and file processing components.

### Individual Component Commands
```bash
# Start Express server with WebSocket camera communication
node startServer_beta.js

# Run video processing pipeline with DaVinci Resolve
python batch.py

# Process videos with MediaPipe pose detection and cropping  
python crop.py

# Convert video formats
python convertFiles.py

# Organize and move processed files
python moveFiles.py

# Install Node.js dependencies
npm install
```

### Testing
Unit tests are available for core components and can be run using Python's built-in unittest framework:

```bash
# Run moveFiles unit tests
python3 -m unittest test_moveFiles.py -v

# Run batch processing unit tests  
python3 -m unittest test_batch.py -v

# Run all tests
python3 -m unittest discover -p "test_*.py" -v
```

Tests cover:
- File organization and movement logic (`moveFiles.py`)
- Video processing pipeline functions (`batch.py`)
- Error handling and edge cases
- Mock-based testing for external dependencies

## Architecture Overview

This is a **sign language video production studio automation system** that processes multi-camera recordings through an AI-enhanced pipeline.

### Core Components

1. **Video Capture Layer** (`startServer_beta.js`)
   - Express.js server with WebSocket support
   - Handles camera communication (L/M/R cameras)
   - Manages incoming video file uploads

2. **DaVinci Resolve Integration** (`batch.py`) 
   - Automates professional video editing workflow
   - Applies Fusion compositions for green screen removal
   - Handles background replacement with gradients
   - Supports multiple processing modes (portrait/landscape, TYD)

3. **AI Processing Pipeline** (`crop.py`)
   - MediaPipe-based pose detection for intelligent video cropping
   - Focuses on sign language interpreter positioning
   - Automated thumbnail generation

4. **File Management System** (`moveFiles.py`, `convertFiles.py`)
   - `moveFiles.py`: Main file organization service that moves video files from cache to organized date-based directory structure
   - `convertFiles.py`: Handles H.264 encoding and format conversion
   - Manages import/export directory structure and file staging

5. **Process Orchestration** (`startupScript.py`)
   - Service monitoring and auto-restart capabilities
   - Coordinates all system components
   - Provides crash recovery

### Key Directories
- `import/` - Video capture staging area
- `export/` - Final processed video outputs  
- `temp/` - Temporary processing files
- `qr/` - QR code assets
- `sc/` - Secondary Python environment
- `logs/` - Individual service log files with automatic rotation

### Settings Files
- `Settings.setting` - Main DaVinci Resolve Fusion composition
- `landscape.setting` - Landscape video processing configuration
- `tyd.setting` - TYD (Thank You Deaf) specific processing settings

### Processing Workflow
1. Videos captured from multiple camera angles
2. Files automatically organized by date/camera
3. DaVinci Resolve applies professional editing (green screen, backgrounds)
4. MediaPipe performs intelligent cropping based on signer pose
5. Videos converted to final formats
6. Upload to external video management servers via API
7. Continuous monitoring ensures pipeline reliability

### External Integrations
- Video render management API (`video_api_client.py`)
- File listing service (`listFiles.php`) 
- Web-based camera control interfaces

### Logging System
Each service managed by `startupScript.py` has its own dedicated log file in the `logs/` directory:
- Individual log files for each service (e.g., `logs/batch.log`, `logs/crop.log`)
- Automatic log rotation when files exceed 10MB
- Timestamped entries for service start/stop/restart events
- Combined stdout/stderr output for comprehensive debugging
- Log files include: mouse.log, moveFiles.log, batch.log, crop.log, server.log, convertFiles.log, rclone.log, listFiles.log

### Core Files Reference
**Primary Components:**
- `moveFiles.py`: Main file organization service (not `moveFiles_backup.py`)
- `batch.py`: Main DaVinci Resolve processing pipeline (not `batch_backup.py`)

**Backup Files:** 
- Backup versions (`*_backup.py`) are retained for reference but are not the active components

### Desktop Tools & Monitoring (`/Users/signlab/Desktop`)

**Camera Monitoring System:**
- `startMonitor.py`: Enhanced PyQt5-based GUI application for comprehensive studio monitoring
  - **Camera Hardware Monitoring**: Real-time USB device detection and camera status monitoring
  - **System Process Monitoring**: RemoteCli + Node.js server status tracking
  - **File Status Dashboard**: Live file count monitoring via API integration (`https://signcollect.nl/listfiles.json`)
  - **Media Formatting Controls**: Safe formatting with eligibility checks and WebSocket progress monitoring
  - **Multi-threaded Operations**: Non-blocking camera shutdown/startup sequences
  - **Built-in Help System**: Troubleshooting guides in Dutch
  - **Serial Number Mapping**: 
    - D4DA001EAC65: Camera M
    - D4DA001EACEA: Camera R  
    - D4DA001ECB4B: Camera A
    - D4DA001EAD5C: Camera L
    - D4DA001EC952: Camera B
  - **Real-time Connectivity**: WebSocket integration (`ws://localhost:8081`) for live updates
  - **Safety Features**: Format-before-recording warnings and eligibility validation

**RemoteCli Integration:**
- `sonyRemote/RemoteCli`: Executable for Sony camera remote control
- `sonyRemote/startServer_beta.js`: Node.js server for camera communication
- Automatic process monitoring and crash recovery
- Force restart capabilities for troubleshooting camera connection issues

**Additional Tools:**
- `RemoteCliMonitor.app`: Automator application for remote monitoring access
- `studioFiles/`: Local studio file storage and staging area

**Common Commands:**
```bash
# Install required Python dependencies (if needed)
pip3 install --break-system-packages requests websocket-client

# Start enhanced camera monitoring GUI
python3 /Users/signlab/Desktop/startMonitor.py

# Manual RemoteCli control
/Users/signlab/Desktop/sonyRemote/RemoteCli

# Start camera server (required for file status and formatting)
node /Users/signlab/Desktop/sonyRemote/startServer_beta.js
```

**Troubleshooting Workflow:**
1. **System Status Check**: Monitor RemoteCli + Node.js server status in unified dashboard
2. **Camera Hardware**: Check USB device detection and camera status table
3. **File Management**: Review file status dashboard for recent dates and eligibility
4. **Process Recovery**: Use "Restart RemoteCli" for individual camera issues
5. **System Recovery**: Use "Force Restart RemoteCli" for system-wide issues
6. **Media Formatting**: Use format controls when eligible (all recent dates OK)
7. **Power Cycle**: Follow complete power cycle procedure for persistent problems
8. **Real-time Monitoring**: Watch console output for WebSocket updates and API responses

This system is designed for continuous operation in a production studio environment with robust error handling and automatic recovery capabilities.