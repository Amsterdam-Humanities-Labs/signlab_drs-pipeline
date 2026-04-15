#!/usr/bin/env python3
"""
Camera Download Controller
Implements the camera interaction flow following the exact pattern from example_websocket.js:
1. Run camerasReconnectStartup() to ensure all cameras are in state 0 (remote mode)
2. Set statusDownload("multiple") and disconnect all cameras
3. Wait for automatic reconnection in content mode via WebSocket
4. Wait for contentsChanged events
5. Auto-trigger downloads via WebSocket
"""

import asyncio
import websockets
import json
import requests
import subprocess
import random
from typing import Optional

class CameraDownloadController:
    def __init__(self):
        self.base_url = "http://localhost:18080"
        self.websocket_url = "ws://localhost:8081"
        self.connected_cameras = {}
        self.websocket = None
        self.download_in_progress = False
        self.download_mode = "single"  # Can be "single" or "multiple"
        self.cameras_reconnect_running = False
        self.websocket_listener_running = False
        self.message_queue = asyncio.Queue()
        self.expected_cameras = 0  # Track expected number of cameras for download completion
        self.completed_cameras = set()  # Track completed downloads
        self.run_count = 0  # Track how many times complete sequence has been run
        
        # Camera ID to name mapping from CLAUDE.md
        self.camera_names = {
            "D4DA001EAC65": "Camera M",
            "D4DA001EACEA": "Camera R", 
            "D4DA001ECB4B": "Camera A",
            "D4DA001EAD5C": "Camera L",
            "D4DA001EC952": "Camera B"
        }
    
    async def connect_websocket(self):
        """Connect to WebSocket server"""
        try:
            self.websocket = await websockets.connect(self.websocket_url)
            print(f"✓ Connected to WebSocket server at {self.websocket_url}")
            return True
        except Exception as e:
            print(f"✗ Failed to connect to WebSocket: {e}")
            return False
    
    async def websocket_listener(self):
        """Single WebSocket listener that puts messages in a queue"""
        self.websocket_listener_running = True
        try:
            async for message in self.websocket:
                data = json.loads(message)
                await self.message_queue.put(data)
        except websockets.exceptions.ConnectionClosed:
            print("WebSocket connection closed")
        except Exception as e:
            print(f"WebSocket listener error: {e}")
        finally:
            self.websocket_listener_running = False

    async def process_websocket_messages(self):
        """Process messages from the queue"""
        while self.websocket_listener_running or not self.message_queue.empty():
            try:
                data = await asyncio.wait_for(self.message_queue.get(), timeout=1.0)
                await self.handle_websocket_message(data)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Message processing error: {e}")
                break
    
    async def handle_websocket_message(self, data):
        """Handle incoming WebSocket messages"""
        handle = data.get('handle')
        camera_id = data.get('cameraId')
        camera_name = self.camera_names.get(camera_id, camera_id)
        
        if handle == "connected":
            self.connected_cameras[camera_id] = {
                'connected': True,
                'sdkMode': data.get('sdkMode'),
                'cameraNumber': data.get('cameraNumber')
            }
            print(f"📷 {camera_name} connected (SDK mode: {data.get('sdkMode')})")
            
        elif handle == "disconnected":
            if camera_id in self.connected_cameras:
                self.connected_cameras[camera_id]['connected'] = False
            print(f"📷 {camera_name} disconnected")
            
            # Auto-reconnect in content mode when in multiple download mode
            # This matches the JavaScript: if (statusDownload() == "multiple") { cA[cId]["switchtoSDK"] = 1; }
            if self.download_mode == "multiple":
                camera_number = data.get('cameraNumber')
                if camera_number:
                    print(f"  🔄 Auto-reconnecting {camera_name} in content mode...")
                    # Delay like in JavaScript: setTimeout(function () { fetchAndProcess('http://localhost:18080/connect_camera_contents_ind', rC.cameraNumber, "json"); }, 1000);
                    asyncio.create_task(self._delayed_reconnect_content_mode(camera_number, camera_name))
                else:
                    print(f"  ⚠️  No camera number provided for {camera_name}, cannot auto-reconnect")
            
        elif handle == "contentsChanged":
            print(f"📁 {camera_name} contents changed - ready for download")
            # Auto-trigger download like the HTML does
            if hasattr(self, 'download_mode') and self.download_mode == "multiple":
                camera_number = data.get('cameraNumber')
                if camera_number:
                    print(f"  🚀 Auto-triggering download for {camera_name}...")
                    # Add delay to prevent overwhelming RemoteCli
                    asyncio.create_task(self._delayed_download_trigger(camera_number, camera_name))
            
        elif handle == "fileDownloaded":
            filename = data.get('file', '').split('/')[-1]
            print(f"⬇️  Downloaded: {filename} from {camera_name}")
            
        elif handle == "completedMultiple":
            print(f"✅ {camera_name} download completed")
            self.completed_cameras.add(camera_id)
            
            # Check if all expected cameras have completed
            if len(self.completed_cameras) >= self.expected_cameras and self.expected_cameras > 0:
                print(f"\n🎉 All {len(self.completed_cameras)} camera downloads completed!")
                self.download_in_progress = False
            
        elif handle in ["timeout", "device_prop_failed"]:
            print(f"⚠️  {camera_name}: {handle}")
    
    def make_request(self, endpoint: str, camera_number: Optional[int] = None):
        """Make HTTP request to camera API"""
        url = f"{self.base_url}/{endpoint}"
        if camera_number is not None:
            url += f"?n={camera_number}"
        
        try:
            response = requests.get(url, timeout=3)
            if response.headers.get('content-type', '').startswith('application/json'):
                return response.json()
            return response.text
        except requests.exceptions.Timeout:
            print(f"⏰ Timeout error for {endpoint} - RemoteCli may not be responding")
            return "TIMEOUT"
        except requests.exceptions.ConnectionError:
            print(f"🔌 Connection error for {endpoint} - RemoteCli may not be running")
            return "CONNECTION_ERROR"
        except Exception as e:
            print(f"Request error for {endpoint}: {e}")
            return None
    
    def kill_remote_cli(self):
        """Kill RemoteCli process when it's not responding"""
        try:
            print("🔧 Killing RemoteCli process...")
            result = subprocess.run(['killall', '-9', 'RemoteCli'], 
                                  capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                print("✅ RemoteCli process killed successfully")
                return True
            else:
                print(f"⚠️  killall returned code {result.returncode}: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            print("⏰ Timeout while trying to kill RemoteCli")
            return False
        except Exception as e:
            print(f"❌ Error killing RemoteCli: {e}")
            return False
    
    def wait_for_user_restart(self):
        """Wait for user to manually restart RemoteCli"""
        print("\n" + "="*60)
        print("🚨 REMOTECLI SERVICE ISSUE DETECTED")
        print("="*60)
        print("The RemoteCli service is not responding to requests.")
        print("This usually means the service needs to be restarted.")
        print("")
        print("Please follow these steps:")
        print("1. Start RemoteCli manually from Desktop")
        print("2. Or run: /Users/signlab/Desktop/sonyRemote/RemoteCli")
        print("3. Wait for cameras to be detected")
        print("4. Press Enter when RemoteCli is running...")
        print("="*60)
        
        input("Press Enter to continue when RemoteCli is restarted: ")
        print("✅ Continuing with camera operations...")
    
    def reset_state_for_new_run(self):
        """Reset all state variables to initial values before starting a new complete sequence"""
        self.run_count += 1
        
        if self.run_count > 1:
            print(f"\n🔄 Resetting state for run #{self.run_count}...")
            
            # Reset global state
            self.download_in_progress = False
            self.download_mode = "single"  # Reset to single mode
            self.cameras_reconnect_running = False
            self.expected_cameras = 0
            self.completed_cameras.clear()
            self.connected_cameras.clear()
            
            # Clear the message queue
            while not self.message_queue.empty():
                try:
                    self.message_queue.get_nowait()
                except:
                    break
            
            print("  ✅ State variables reset to initial values")
            print("  📡 Download mode reset to 'single'")
            print("  🗑️  Message queue cleared")
            print("  📊 Camera tracking reset")
        else:
            print(f"\n🚀 Starting first run of complete sequence...")
    
    async def ensure_cameras_in_remote_mode(self):
        """Ensure all cameras are in remote mode (state=0) before starting new sequence"""
        print("🔍 Checking camera modes before starting sequence...")
        
        status = self.make_request("check_status")
        if not status or status in ["TIMEOUT", "CONNECTION_ERROR"]:
            return False
        
        cameras_needing_disconnect = []
        for camera in status:
            camera_id = camera.get('camera_id')
            state = camera.get('state')
            camera_name = self.camera_names.get(str(camera_id), f"Camera {camera_id}")
            
            if state == 1:  # Camera in content mode
                cameras_needing_disconnect.append((camera_id, camera_name))
                print(f"  ⚠️  {camera_name} is in content mode (state=1)")
        
        if cameras_needing_disconnect:
            print("🔧 Disconnecting cameras from content mode...")
            for camera_id, camera_name in cameras_needing_disconnect:
                print(f"  📤 Disconnecting {camera_name}...")
                self.make_request("camera_disconnect_ind", camera_id)
                await asyncio.sleep(2)  # Longer delay between camera operations
            
            # Wait longer for disconnections to complete
            await asyncio.sleep(5)
            print("✅ Camera mode cleanup completed")
        else:
            print("✅ All cameras already in correct mode")
        
        return True
    
    async def _delayed_reconnect_content_mode(self, camera_number: int, camera_name: str):
        """Reconnect camera in content mode after 1 second delay (matches JavaScript setTimeout)"""
        await asyncio.sleep(1)  # 1000ms delay like in JavaScript
        if self.make_request("connect_camera_contents_ind", camera_number):
            print(f"  ✅ {camera_name} reconnection command sent")
        else:
            print(f"  ❌ Failed to reconnect {camera_name} in content mode")
    
    async def _delayed_download_trigger(self, camera_number: int, camera_name: str):
        """Trigger download with delay to prevent overwhelming RemoteCli"""
        # Add random delay between 1-3 seconds to spread out requests
        delay = random.uniform(1.0, 3.0)
        await asyncio.sleep(delay)
        
        result = self.make_request("ophalen_movie_multiple", camera_number)
        if result and result not in ["TIMEOUT", "CONNECTION_ERROR"]:
            print(f"  ✅ {camera_name} download triggered successfully")
            if not self.download_in_progress:
                self.download_in_progress = True
        elif result in ["TIMEOUT", "CONNECTION_ERROR"]:
            print(f"  ⚠️  {camera_name} download trigger failed - RemoteCli overloaded, will retry...")
            # Retry once after longer delay
            await asyncio.sleep(5)
            retry_result = self.make_request("ophalen_movie_multiple", camera_number)
            if retry_result and retry_result not in ["TIMEOUT", "CONNECTION_ERROR"]:
                print(f"  ✅ {camera_name} download retry successful")
            else:
                print(f"  ❌ {camera_name} download retry failed - RemoteCli may need restart")
        else:
            print(f"  ❌ {camera_name} download trigger failed")
    
    async def cameras_reconnect_startup(self, max_iterations=10):
        """
        Implement camerasReconnectStartup() from the JavaScript
        Ensures all cameras are in proper state (status=true, state=0)
        """
        print("🔄 Starting camera reconnection startup sequence...")
        self.cameras_reconnect_running = True
        
        for iteration in range(max_iterations):
            print(f"\n--- Iteration {iteration + 1}/{max_iterations} ---")
            
            camera_success = 0
            total_cameras = 0
            found_success = False
            
            # Step 1: Check status
            print("🔍 Checking camera status...")
            check_status = self.make_request("check_status")
            
            if check_status in ["TIMEOUT", "CONNECTION_ERROR"]:
                print("🚨 RemoteCli service not responding - killing immediately...")
                self.kill_remote_cli()
                self.wait_for_user_restart()
                # Reset iteration counter to start fresh after restart
                break
            elif not check_status:
                print("❌ Failed to get camera status")
                continue
                
            # Count total cameras
            total_cameras = len(check_status)
            print(f"Found {total_cameras} cameras")
            
            # Process each camera based on its status
            for item in check_status:
                camera_id = item.get('camera_id')
                status = item.get('status')
                state = item.get('state')
                camera_name = self.camera_names.get(str(camera_id), f"Camera {camera_id}")
                
                print(f"  📷 {camera_name}: status={status}, state={state}")
                
                if status == True and state == 0:
                    # Camera is in correct state (remote mode)
                    camera_success += 1
                    print(f"    ✅ {camera_name} is ready")
                    
                elif status == "fail_please_use_disconnect":
                    # Need to disconnect first
                    print(f"    🔧 {camera_name} needs disconnect, trying connect_camera_remote_ind...")
                    disconnect_result = self.make_request("connect_camera_remote_ind", camera_id)
                    await asyncio.sleep(3)  # Longer delay to prevent device_prop_failed spam
                    
                    if disconnect_result and len(disconnect_result) > 0:
                        if disconnect_result[0].get('status') == "disconnect_failed_try_connect":
                            print(f"    🔄 Retrying connect for {camera_name}...")
                            self.make_request("connect_camera_remote_ind", camera_id)
                            await asyncio.sleep(3)  # Longer delay
                            
                elif not status:
                    # Camera not connected, try to connect
                    print(f"    🔌 Connecting {camera_name} in remote mode...")
                    self.make_request("connect_camera_remote_ind", camera_id)
                    await asyncio.sleep(3)  # Longer delay to prevent device_prop_failed spam
                    
                elif state == 1 and status:
                    # Camera connected but in wrong state (content mode), disconnect it
                    print(f"    🔄 {camera_name} in content mode, disconnecting...")
                    self.make_request("camera_disconnect_ind", camera_id)
                    await asyncio.sleep(3)  # Longer delay
            
            # Check if all cameras are ready
            if camera_success == total_cameras:
                found_success = True
                print(f"🎉 All {total_cameras} cameras are online and ready!")
                break
            else:
                print(f"⏳ {camera_success}/{total_cameras} cameras ready, retrying...")
                await asyncio.sleep(5)  # Longer wait to let cameras stabilize
        
        self.cameras_reconnect_running = False
        
        if found_success:
            print("✅ Camera reconnection startup completed successfully!")
            return True
        else:
            print("❌ Camera reconnection startup failed after maximum iterations")
            return False
    
    async def start_download_sequence(self):
        """
        Implement the download sequence after cameras are ready:
        1. Set statusDownload("multiple") 
        2. Disconnect all cameras
        3. Wait for automatic reconnection in content mode
        4. Downloads will be triggered automatically via WebSocket
        """
        print("\n🚀 Starting download sequence...")
        
        # First, get current camera status to know how many cameras to expect
        status = self.make_request("check_status")
        if status:
            # Count cameras that are connected and ready
            ready_cameras = [cam for cam in status if cam.get('status') == True]
            self.expected_cameras = len(ready_cameras)
            print(f"📊 Expecting downloads from {self.expected_cameras} cameras")
        else:
            print("⚠️  Could not determine camera count, setting expected to 1")
            self.expected_cameras = 1
        
        # Reset completed cameras tracking
        self.completed_cameras.clear()
        
        # Step 1: Set download mode to multiple
        print("📡 Setting download mode to 'multiple'...")
        self.download_mode = "multiple"
        
        # Step 2: Disconnect all cameras (this triggers automatic reconnection in content mode)
        print("🔌 Disconnecting all cameras...")
        result = self.make_request("camera_disconnect")
        
        if result:
            print("✅ Camera disconnect command sent")
            print("⏳ Cameras will automatically reconnect in content mode via WebSocket...")
            print("📥 Downloads will be triggered automatically when contentsChanged events are received")
            self.download_in_progress = True
            return True
        else:
            print("❌ Failed to disconnect cameras")
            return False
    
    async def monitor_downloads(self):
        """Monitor download progress by tracking completedMultiple messages"""
        print(f"\n📊 Monitoring download progress for {self.expected_cameras} cameras...")
        print("Press Ctrl+C to stop monitoring\n")
        
        try:
            while self.download_in_progress:
                # The message processing is handled by process_websocket_messages()
                # The download_in_progress flag will be set to False automatically
                # when all expected cameras complete in handle_websocket_message()
                await asyncio.sleep(1)
                        
        except KeyboardInterrupt:
            print("\n⏹️  Monitoring stopped by user")
            self.download_in_progress = False
        
        print("\n📱 Returning to main menu...")
        # Small delay to ensure all WebSocket messages are processed
        await asyncio.sleep(2)

    async def run_complete_download_sequence(self):
        """
        Wrapper function to run the complete download sequence programmatically.
        This is equivalent to selecting Option 1 from the menu.
        
        Returns:
            bool: True if successful, False if failed
        """
        print("="*60)
        print("🚀 STARTING COMPLETE CAMERA DOWNLOAD SEQUENCE")
        print("="*60)
        
        # Reset state for new run
        self.reset_state_for_new_run()
        
        # Create WebSocket tasks
        listener_task = asyncio.create_task(self.websocket_listener())
        processor_task = asyncio.create_task(self.process_websocket_messages())
        
        try:
            # Step 0: Ensure cameras are in correct mode
            mode_check = await self.ensure_cameras_in_remote_mode()
            if not mode_check:
                print("❌ Camera mode check failed")
                return False
            
            # Step 1: Reconnect startup
            startup_success = await self.cameras_reconnect_startup()
            if not startup_success:
                print("❌ Camera startup failed")
                return False
            
            # Step 2: Start download sequence
            download_success = await self.start_download_sequence()
            if not download_success:
                print("❌ Download sequence failed to start")
                return False
            
            # Step 3: Monitor downloads
            await self.monitor_downloads()
            
            print("="*60)
            print("✅ COMPLETE DOWNLOAD SEQUENCE FINISHED SUCCESSFULLY")
            print("="*60)
            return True
            
        except KeyboardInterrupt:
            print("\n⏹️  Operation cancelled by user")
            return False
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            return False
        finally:
            # Clean up
            self.websocket_listener_running = False
            listener_task.cancel()
            processor_task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(listener_task, processor_task, return_exceptions=True), 
                    timeout=2.0
                )
            except asyncio.TimeoutError:
                pass

def show_menu():
    """Display the main menu"""
    print("\n" + "="*60)
    print("📷 CAMERA DOWNLOAD CONTROLLER")
    print("="*60)
    print("1. Run complete sequence (reconnect startup + download)")
    print("2. Run camera reconnect startup only")
    print("3. Run download sequence only (cameras must be ready)")
    print("4. Check camera status")
    print("5. Monitor WebSocket messages")
    print("6. Exit")
    print("="*60)

async def main():
    controller = CameraDownloadController()
    
    # Connect to WebSocket first
    if not await controller.connect_websocket():
        print("Cannot proceed without WebSocket connection")
        return
    
    while True:
        show_menu()
        choice = input("Select option (1-6): ").strip()
        
        if choice == "1":
            # Complete sequence - use the wrapper function
            await controller.run_complete_download_sequence()
                
        elif choice == "2":
            # Reconnect startup only
            await controller.cameras_reconnect_startup()
            
        elif choice == "3":
            # Download sequence only
            listener_task = asyncio.create_task(controller.websocket_listener())
            processor_task = asyncio.create_task(controller.process_websocket_messages())
            try:
                success = await controller.start_download_sequence()
                if success:
                    await controller.monitor_downloads()
            except KeyboardInterrupt:
                print("\n⏹️  Operation cancelled")
            finally:
                controller.websocket_listener_running = False
                listener_task.cancel()
                processor_task.cancel()
                try:
                    await asyncio.wait_for(asyncio.gather(listener_task, processor_task, return_exceptions=True), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                
        elif choice == "4":
            # Check status only
            status = controller.make_request("check_status")
            if status in ["TIMEOUT", "CONNECTION_ERROR"]:
                print("🚨 RemoteCli service not responding - killing immediately...")
                controller.kill_remote_cli()
                controller.wait_for_user_restart()
            elif status:
                print(f"\n📊 Camera Status:")
                for item in status:
                    camera_id = item.get('camera_id')
                    camera_name = controller.camera_names.get(str(camera_id), f"Camera {camera_id}")
                    status_str = "✓" if item.get('status') else "✗"
                    state = item.get('state', 'unknown')
                    print(f"  {status_str} {camera_name}: status={item.get('status')}, state={state}")
            else:
                print("❌ Failed to get camera status")
                
        elif choice == "5":
            # Monitor WebSocket only
            print("🎧 Listening to WebSocket messages (Press Ctrl+C to stop)...")
            listener_task = asyncio.create_task(controller.websocket_listener())
            processor_task = asyncio.create_task(controller.process_websocket_messages())
            try:
                # Just run the processors
                await asyncio.gather(listener_task, processor_task)
            except KeyboardInterrupt:
                print("\n⏹️  Stopped listening")
            finally:
                controller.websocket_listener_running = False
                listener_task.cancel()
                processor_task.cancel()
                try:
                    await asyncio.wait_for(asyncio.gather(listener_task, processor_task, return_exceptions=True), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                
        elif choice == "6":
            print("👋 Goodbye!")
            break
            
        else:
            print("❌ Invalid option, please try again")
    
    # Close WebSocket connection
    if controller.websocket:
        await controller.websocket.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")