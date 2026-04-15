import os, sys, glob, shutil, time, json, subprocess, re, gc
from datetime import datetime, timedelta
sys.path.insert(0, '/Users/signlab/drs/shared')
from python_get_resolve import GetResolve
from pathlib import Path
from video_api_client import VideoAPIClient  # Import the API client
from signcollect_monitor import SignCollectMonitor  # Import the monitor client

# Initialize the monitor
monitor = SignCollectMonitor(
    client_id='drs-batch-processor',
    client_name='DRS Batch Processor',
    description='DaVinci Resolve video rendering pipeline',
    heartbeat_interval=3600
)


def clean_rendered_filename(filename):
    """
    Clean up DaVinci Resolve's auto-appended suffixes from filename.
    DaVinci may add '_1', '_2', ' 1', ' 2', '_02418924' etc. before the extension.
    Example: 'L20241217_1030_1.mp4' -> 'L20241217_1030.MP4'
    Example: 'M20241217_1030 1.mp4' -> 'M20241217_1030.MP4'
    Example: 'M20260112_9403_02418924.mp4' -> 'M20260112_9403.MP4'
    """
    # Pattern to match: base_name + optional suffix (_N or space N or _NNNNNNNN) + extension
    # Original filename pattern: [L|M|R]YYYYMMDD_HHMM.MP4
    match = re.match(r'^([LMR]\d{8}_\d{4})(?:_\d+| \d+)?\.mp4$', filename, re.IGNORECASE)
    if match:
        base_name = match.group(1)
        return f"{base_name}.MP4"  # Return with original .MP4 extension
    return filename  # Return unchanged if pattern doesn't match

# Ensure output is flushed immediately to logs
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

def handle_mount_failure():
    """Handle mount failure by unmounting the rclone mount"""
    mount_path = "/Users/signlab/signCollect"
    try:
        print(f"Unmounting {mount_path} due to mount failure...")
        result = subprocess.run(['umount', '-f', mount_path],
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
        if result.returncode == 0:
            print(f"Successfully unmounted {mount_path}")
        else:
            print(f"Failed to unmount {mount_path}: {result.stderr}")
    except Exception as e:
        print(f"Error unmounting {mount_path}: {e}")

def trigger_mount_recovery():
    """Trigger mount recovery by unmounting and signaling for remount"""
    mount_path = "/Users/signlab/signCollect"
    marker_file = "/Users/signlab/drs/mount_recovery_needed"
    
    try:
        print(f"Triggering mount recovery for {mount_path}...")
        
        # Create marker file to signal recovery is needed
        with open(marker_file, 'w') as f:
            f.write(f"{datetime.now().isoformat()}\n")
        
        # Unmount the rclone mount
        print(f"Unmounting {mount_path}...")
        result = subprocess.run(['umount', '-f', mount_path],
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)

        if result.returncode == 0:
            print(f"Successfully unmounted {mount_path}")
        else:
            print(f"Unmount returned non-zero code: {result.stderr}")
        
        # Wait 30 seconds for clean disconnection
        print("Waiting 30 seconds for clean mount disconnection...")
        time.sleep(30)
        
        return True
        
    except Exception as e:
        print(f"Error during mount recovery trigger: {e}")
        return False

def wait_for_mount_recovery():
    """Wait for mount to be recovered by startupScript.py"""
    mount_path = "/Users/signlab/signCollect"
    marker_file = "/Users/signlab/drs/mount_recovery_needed"
    max_wait_time = 300  # 5 minutes max wait
    check_interval = 5
    wait_time = 0
    
    print("Waiting for mount recovery to complete...")
    
    while wait_time < max_wait_time:
        # Check if marker file still exists (removed when recovery complete)
        if not os.path.exists(marker_file):
            # Verify mount is actually working
            test_path = os.path.join(mount_path, "AIHR-FGW-TEST-SIGNLAB (Projectfolder)")
            if os.path.exists(test_path):
                try:
                    # Try to list directory to ensure mount is responsive
                    contents = os.listdir(test_path)
                    if contents:
                        print(f"Mount recovery complete! Mount is responsive.")
                        return True
                except Exception as e:
                    print(f"Mount exists but not responsive yet: {e}")
            
        time.sleep(check_interval)
        wait_time += check_interval
        
        if wait_time % 30 == 0:  # Progress update every 30 seconds
            print(f"Still waiting for mount recovery... ({wait_time}s elapsed)")
    
    print(f"Mount recovery timeout after {max_wait_time} seconds")
    return False

def verify_mount_health():
    """Verify that the rclone mount is healthy and accessible"""
    mount_path = "/Users/signlab/signCollect"
    test_path = os.path.join(mount_path, "AIHR-FGW-TEST-SIGNLAB (Projectfolder)")
    
    try:
        # Check if mount path exists
        if not os.path.exists(mount_path):
            print(f"Mount path does not exist: {mount_path}")
            return False
        
        # Check if expected directory exists
        if not os.path.exists(test_path):
            print(f"Expected directory not found in mount: {test_path}")
            return False
        
        # Try to access files to ensure mount is responsive
        try:
            contents = os.listdir(test_path)
            if not contents:
                print(f"Mount directory appears empty, may be unmounted: {test_path}")
                return False
            
            print(f"Mount health check passed. Found {len(contents)} items in {test_path}")
            return True
            
        except (OSError, PermissionError) as e:
            print(f"Mount appears unresponsive: {e}")
            return False
            
    except Exception as e:
        print(f"Error during mount health check: {e}")
        return False

def rsync_copy(source, destination, retries=2):
    """
    Use rsync to copy files from rclone mounted drives for better performance.
    Includes retry logic similar to the original shutil.copy2 implementation.
    """
    # Ensure destination directory exists
    dest_path = Path(destination)
    dest_dir = dest_path.parent
    if not dest_dir.exists():
        os.makedirs(dest_dir, exist_ok=True)
    
    mount_recovery_triggered = False
    
    for attempt in range(retries):
        try:
            # Build rsync command with appropriate flags
            # -a: archive mode (preserves permissions, timestamps, etc.)
            # -v: verbose
            # --progress: show progress during transfer
            cmd = ["rsync", "-av", "--progress", str(source), str(destination)]
            
            # Run rsync command
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
            
            # If successful, check file size
            if os.path.exists(destination) and os.path.getsize(destination) == 0:
                # Check if source file was actually non-empty
                if os.path.exists(source) and os.path.getsize(source) > 0:
                    print(f"WARNING: Copied file {destination} is 0KB but source is {os.path.getsize(source)} bytes")
                    print("Mount appears to be failing - unmounting /Users/signlab/signCollect")
                    handle_mount_failure()
                    raise OSError("Mount failure detected - destination file is 0KB")
            
            return True
            
        except subprocess.CalledProcessError as e:
            error_msg = f"rsync failed for {source} to {destination}"
            if e.stderr:
                error_msg += f": {e.stderr}"
            
            # Check for "No such file or directory" error
            if "No such file or directory" in str(e.stderr) or "No such file or directory" in error_msg:
                print(f"FILE NOT FOUND ERROR detected: {error_msg}")
                
                # Only trigger mount recovery once per rsync_copy call
                if not mount_recovery_triggered:
                    print("Initiating mount recovery due to file not found error...")
                    mount_recovery_triggered = True
                    
                    if trigger_mount_recovery():
                        if wait_for_mount_recovery():
                            print("Mount recovery successful. Retrying rsync operation...")
                            # Reset attempt counter to give full retries after recovery
                            attempt = -1
                            continue
                        else:
                            print("Mount recovery failed or timed out.")
                            raise OSError("Mount recovery failed - cannot access files")
                
            print(f"Attempt {attempt + 1}/{retries} - {error_msg}")
            
            if attempt < retries - 1:
                print(f"Retrying in 2 seconds...")
                time.sleep(2)
            else:
                print(f"All attempts failed for {source}")
                raise OSError(f"rsync failed after {retries} attempts: {error_msg}")
                
    return False

def get_video_orientation(filepath):
    if not os.path.isfile(filepath):
        print(f"File not found: {filepath}")
        
        # Check if this is a mount-related issue
        if "/Users/signlab/signCollect/" in str(filepath):
            print("File not found error appears to be mount-related. Checking mount health...")
            if not verify_mount_health():
                print("Mount appears unhealthy. Triggering mount recovery...")
                if trigger_mount_recovery() and wait_for_mount_recovery():
                    print("Mount recovery completed. Rechecking file...")
                    if os.path.isfile(filepath):
                        print(f"File now accessible after mount recovery: {filepath}")
                    else:
                        print(f"File still not accessible after mount recovery: {filepath}")
                        return None, "file_not_found_after_recovery"
                else:
                    print("Mount recovery failed")
                    return None, "mount_recovery_failed"
        
        return None, "file_not_found"

    cmd = ["/opt/homebrew/bin/mediainfo", "--Output=JSON", filepath]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
    data = json.loads(result.stdout)

    try:
        # Find the video track
        video_track = next(track for track in data["media"]["track"] if track["@type"] == "Video")
        width = int(video_track.get("Width", 0))
        height = int(video_track.get("Height", 0))
        rotation = int(float(video_track.get("Rotation", 0)))

        # Adjust for rotation
        if rotation in [90, 270]:
            width, height = height, width

        orientation = "portrait" if height > width else "landscape"
        return rotation, orientation
    except Exception as e:
        print(f"Error getting viceo orientation: {e}")
        return 270, "portrait"

def check_user_idle_time():
    """
    Check how long the user has been idle (KEYBOARD ONLY) on macOS.
    This reads from a timestamp file written by keyboard_monitor.py
    Returns the idle time in seconds, or None if unable to determine.
    """
    timestamp_file = "/Users/signlab/drs/last_keyboard_activity.txt"

    try:
        # Check if timestamp file exists
        if not os.path.exists(timestamp_file):
            print(f"Keyboard activity timestamp file not found: {timestamp_file}")
            print("Make sure keyboard_monitor.py is running in the background.")
            return None

        # Read the last keyboard activity timestamp
        with open(timestamp_file, 'r') as f:
            last_activity_timestamp = float(f.read().strip())

        # Calculate idle time
        current_time = time.time()
        idle_seconds = current_time - last_activity_timestamp

        return idle_seconds

    except ValueError as e:
        print(f"Error parsing timestamp file: {e}")
        return None
    except Exception as e:
        print(f"Error checking keyboard idle time: {e}")
        return None

def is_user_idle_long_enough(required_idle_seconds=1):
    """
    Check if the user has been idle (KEYBOARD ONLY) for at least the required time.
    Default is 3600 seconds (1 hour).
    Returns True if user has been idle long enough, False otherwise.
    NOTE: Requires keyboard_monitor.py to be running in the background.
    """
    idle_time = check_user_idle_time()

    if idle_time is None:
        print("WARNING: Could not determine keyboard idle time. Skipping processing.")
        print("Make sure keyboard_monitor.py is running in the background.")
        return False  # Don't process if we can't determine idle time

    idle_minutes = int(idle_time / 60)
    required_minutes = int(required_idle_seconds / 60)

    if idle_time >= required_idle_seconds:
        print(f"Keyboard has been idle for {idle_minutes} minutes (required: {required_minutes} minutes). OK to proceed.")
        return True
    else:
        print(f"Keyboard activity detected. Idle time: {idle_minutes} minutes (required: {required_minutes} minutes). Skipping processing.")
        return False

def open_davinci_minimized():
    """Open DaVinci Resolve in minimized mode using AppleScript"""
    cmd = [
        "osascript",
        "-e", 'tell application "DaVinci Resolve" to launch',
        "-e", 'delay 20',
        "-e", 'tell application "System Events" to set visible of process "DaVinci Resolve" to false'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode != 0:
        print(f"Warning: Could not minimize DaVinci Resolve: {result.stderr}")
    else:
        print("DaVinci Resolve opened in minimized mode")

def minimize_davinci():
    """Minimize DaVinci Resolve window using AppleScript"""
    cmd = [
        "osascript",
        "-e", 'tell application "System Events" to set visible of process "DaVinci Resolve" to false'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode != 0:
        print(f"Warning: Could not minimize DaVinci Resolve: {result.stderr}")
    else:
        print("DaVinci Resolve minimized")

def get_resolve_with_retry(max_retries=10):
    """Try to get DaVinci Resolve with retry logic"""
    resolve = GetResolve()
    retry_count = 0
    
    while not resolve and retry_count < max_retries:
        retry_count += 1
        print(f"Retry attempt {retry_count}/{max_retries} to open DaVinci Resolve...")
        open_davinci_minimized()
        time.sleep(10)
        resolve = GetResolve()
        
    return resolve

def should_skip_file(filename, post_dir):
    """Check if a .skip file exists for the given MP4 filename"""
    base_name = os.path.splitext(filename)[0]
    skip_file_path = Path(post_dir) / f"{base_name}.skip"
    return skip_file_path.exists()

def create_skip_file(filename, post_dir, reason):
    """Create a .skip file with timestamp and failure reason"""
    base_name = os.path.splitext(filename)[0]
    skip_file_path = Path(post_dir) / f"{base_name}.skip"
    
    skip_data = {
        "timestamp": datetime.now().isoformat(),
        "original_filename": filename,
        "reason": reason,
        "base_name": base_name
    }
    
    try:
        with open(skip_file_path, 'w') as f:
            json.dump(skip_data, f, indent=2)
        print(f"Created skip file: {skip_file_path} (reason: {reason})")
        return True
    except Exception as e:
        print(f"Failed to create skip file {skip_file_path}: {e}")
        return False



# Global variables to track current processing state for crash handling
current_processing_filename = None
current_processing_post_dir = None

def main():
    global current_processing_filename, current_processing_post_dir

    # Clean up memory from previous runs
    gc.collect()

    homedir = Path("/Users/signlab/")
    base_dir = Path("/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles")
    import_dir = homedir / "drs/import"
    export_dir = homedir / "drs/export"
    setting_path = homedir / "drs/config/Settings.setting"
    setting_path = str(setting_path)
    landscape_setting_path = homedir / "drs/config/landscape.setting"
    landscape_setting_path = str(landscape_setting_path)

    print(import_dir)
    print(export_dir)
    
    # Verify mount health before starting processing
    print("Verifying mount health before processing...")
    if not verify_mount_health():
        print("Mount is unhealthy. Attempting recovery...")
        if trigger_mount_recovery() and wait_for_mount_recovery():
            print("Mount recovery successful. Proceeding with processing.")
        else:
            print("Mount recovery failed. Cannot proceed with processing.")
            print("Sleeping for 1 minute before retrying...")
            time.sleep(60)  # 1 minute  # Sleep for 30 minutes instead of exiting
            return

    # Only scan directories from the past 3 days
    date_threshold = datetime.now() - timedelta(days=7)  # Look back 1 week
    all_items = os.listdir(base_dir)
    date_folders = []
    for item in all_items:
        folder_path = base_dir / item
        if folder_path.is_dir():
            # print(f"processing folder:" {folder_path})
            try:
                folder_date = datetime.strptime(item, "%Y-%m-%d")
                # Only include folders from the past 3 days
                if folder_date >= date_threshold:
                    date_folders.append(item)
            except Exception:
                pass

    if not date_folders:
        print(f"No date folders found in the past 3 days (since {date_threshold.strftime('%Y-%m-%d')}).")
        print("Sleeping for 1 minute before checking again...")
        time.sleep(60)  # 1 minute  # Sleep for 30 minutes instead of exiting
        return

    # Check if there are any raw files to process before opening DaVinci Resolve
    files_to_process = []
    for date_folder in sorted(date_folders, reverse=True):
        raw_dir = base_dir / date_folder / "raw"
        post_dir = base_dir / date_folder / "post_noncropped"
        
        raw_files = glob.glob(str(raw_dir / "[LMR]202*.MP4"))
        for raw_file in raw_files:
            raw_file_path = Path(raw_file)
            filename = raw_file_path.name
            dest_post = post_dir / filename
            
            # Skip if already processed
            if dest_post.exists():
                continue
                
            # Check date matching
            date_in_filename = filename[1:9]
            date_in_folder = date_folder.replace("-", "")
            if date_in_filename != date_in_folder:
                continue
                
            files_to_process.append((raw_file, date_folder, post_dir))
    
    if not files_to_process:
        monitor.send_heartbeat_with_stats(
            status='idle',
            message='No new raw files found to process',
            stats={'files_found': 0}
        )
        print("No new raw files found to process.")
        print("Sleeping for 1 minute before checking again...")
        time.sleep(60)  # 1 minute  # Sleep for 30 minutes instead of exiting
        return

    if len(files_to_process) <= 1:
        monitor.send_heartbeat_with_stats(
            status='waiting',
            message=f'Found only {len(files_to_process)} file(s), need at least 2',
            stats={'files_found': len(files_to_process)}
        )
        print(f"Found only {len(files_to_process)} file(s) to process. Need at least 2 files to start DaVinci Resolve.")
        print("Sleeping for 1 minute before checking again...")
        time.sleep(60)  # 1 minute  # Sleep for 30 minutes instead of exiting
        return

    # Sort files to prioritize M camera files first, then L and R
    def get_camera_priority(file_tuple):
        filename = os.path.basename(file_tuple[0])
        if filename.startswith('M'):
            return 0  # M files first
        elif filename.startswith('L'):
            return 1  # L files second
        else:  # R files
            return 2  # R files last

    files_to_process.sort(key=get_camera_priority)
    print(f"Files sorted by camera priority (M first, then L, then R)")

    # Limit batch size to prevent "Too many open files" error
    BATCH_LIMIT = 50
    if len(files_to_process) > BATCH_LIMIT:
        print(f"Limiting to {BATCH_LIMIT} files per batch (out of {len(files_to_process)} total)")
        files_to_process = files_to_process[:BATCH_LIMIT]

    # TEMPORARILY DISABLED: Check if user has been idle for at least 1 hour before starting DaVinci
    if not is_user_idle_long_enough(required_idle_seconds=1):
        print("User activity detected. Skipping processing this cycle.")
        print("Sleeping for 1 minute before checking again...")
        time.sleep(60)  # 1 minute  # Sleep for 30 minutes instead of continuing
        return

    print(f"Found {len(files_to_process)} files to process. Opening DaVinci Resolve...")
    
    # Only open DaVinci Resolve after confirming there are enough files to process
    resolve = get_resolve_with_retry()
    if not resolve:
        print(f"Failed to open DaVinci Resolve after multiple attempts. Exiting.")
        return

    # Try to get project manager, reopening resolve if it fails
    try:
        projectManager = resolve.GetProjectManager()
    except AttributeError:
        print("Lost connection to DaVinci Resolve. Attempting to reopen...")
        resolve = get_resolve_with_retry()
        if not resolve:
            print("Failed to reopen DaVinci Resolve. Exiting.")
            return
        projectManager = resolve.GetProjectManager()

    # Clean up import directory before processing
    for f in os.listdir(import_dir):
        file_path = import_dir / f
        if file_path.is_file():
            os.remove(file_path)

    total_processed = 0
    total_files = len(files_to_process)
    for raw_file, date_folder, post_dir in files_to_process:
        # Send heartbeat with progress stats every file
        monitor.send_heartbeat_with_stats(
            status='processing',
            message=f'Processing file {total_processed + 1}/{total_files}',
            stats={'processed': total_processed, 'total': total_files, 'current_file': os.path.basename(raw_file)}
        )
        if not post_dir.exists():
            os.makedirs(post_dir)
            
        raw_file_path = Path(raw_file)
        filename = raw_file_path.name
        
        # Set global variables for crash handling
        current_processing_filename = filename
        current_processing_post_dir = post_dir
        
        # DISABLED: Skip file checks temporarily disabled
        # if should_skip_file(filename, post_dir):
        #     print(f"Skipping {filename} - skip file exists (previously failed)")
        #     continue
        
        # Check video orientation
        rotation, orientation = get_video_orientation(raw_file)
        
        # Handle mount-related failures in orientation check
        if orientation in ["file_not_found_after_recovery", "mount_recovery_failed"]:
            print(f"Skipping {filename} due to mount issues: {orientation}")
            create_skip_file(filename, post_dir, f"Mount issues during orientation check: {orientation}")
            continue
        elif orientation == "file_not_found":
            print(f"File not accessible, skipping: {filename}")
            create_skip_file(filename, post_dir, "File not accessible during orientation check")
            continue
        
        # Determine which project to use based on orientation
        project_name = "landscape" if orientation == "landscape" else "lala6"
        
        if orientation != "portrait" and filename.startswith("M"):
            print(f"Skipping {filename} - {orientation} orientation detected")
            continue

        print(f"Processing file: {filename} (orientation: {orientation}, using project: {project_name})")
        temp_import_path = import_dir / filename
        try:
            rsync_copy(raw_file, temp_import_path)
            minimize_davinci()  # Minimize DaVinci Resolve after copying file
        except OSError as e:
            if "Mount failure detected" in str(e) or "Mount recovery failed" in str(e):
                print(f"MOUNT FAILURE DETECTED for {filename}: {e}")
                print("Skipping this file and continuing with next file.")
                create_skip_file(filename, post_dir, f"Mount failure during file copy: {e}")
            else:
                print(f"rsync copy failed for {filename}. Skipping. Error: {e}")
                create_skip_file(filename, post_dir, f"rsync copy failed: {e}")
            continue

        # Wrap this section in try/except to handle potential resolve connection issues
        try:
            resolve = GetResolve()
            if not resolve:
                print("Lost connection to DaVinci Resolve. Attempting to reopen...")
                resolve = get_resolve_with_retry()
                if not resolve:
                    print("Failed to reopen DaVinci Resolve. Exiting.")
                    return
                    
            projectManager = resolve.GetProjectManager()
            project = projectManager.GetCurrentProject()
            
            # Check if we need to switch projects based on orientation
            if not project or project.GetName() != project_name:
                print(f"Opening project '{project_name}' for {orientation} video...")
                project = projectManager.LoadProject(project_name)
                if not project:
                    print(f"Unable to open project '{project_name}'. Exiting.")
                    return
                    
        except AttributeError:
            print("Lost connection to DaVinci Resolve. Attempting to reopen...")
            resolve = get_resolve_with_retry()
            if not resolve:
                print("Failed to reopen DaVinci Resolve. Exiting.")
                return
            projectManager = resolve.GetProjectManager()
            project = projectManager.GetCurrentProject()
            
            # Check if we need to switch projects based on orientation
            if not project or project.GetName() != project_name:
                print(f"Opening project '{project_name}' for {orientation} video...")
                project = projectManager.LoadProject(project_name)
                if not project:
                    print(f"Unable to open project '{project_name}'. Exiting.")
                    return

        project.DeleteAllRenderJobs()
        base_name = os.path.splitext(filename)[0]
        
        # Check if the expected output file already exists before rendering
        expected_output = post_dir / f"{base_name}.mp4"
        if expected_output.exists():
            print(f"Rendered file {base_name}.mp4 already exists in post directory. Skipping rendering.")
            # Clean up the temporary import file
            if temp_import_path.exists():
                os.remove(temp_import_path)
            continue
            
        mediapool = project.GetMediaPool()

        # Function to get all clips from all folders
        def get_all_clips(folder, clips_list):
            # Add clips from current folder
            clips_list.extend(folder.GetClipList())
            
            # Process subfolders
            for subfolder in folder.GetSubFolderList():
                get_all_clips(subfolder, clips_list)
            
            return clips_list
        
                # Get all clips from the entire media pool
        root_folder = mediapool.GetRootFolder()
        all_clips = get_all_clips(root_folder, [])

        # Delete all clips at once
        if all_clips:
            success = mediapool.DeleteClips(all_clips)
            if success:
                print(f"Successfully deleted {len(all_clips)} clips")
            else:
                print("Failed to delete clips")
        else:
            print("No clips found in media pool")
        video_path_source = import_dir
        #convert video_path_source to a string
        video_path_source = str(video_path_source)
        
        # Wait for the file to be available in import directory
        wait_time = 0
        max_wait = 30  # Maximum wait time in seconds
        check_interval = 2  # Check every 2 seconds
        
        file_to_check = temp_import_path  # The file we just copied
        while wait_time < max_wait:
            if file_to_check.exists():
                print(f"File {filename} is available in import directory")
                break
            else:
                print(f"Waiting for {filename} to be available... ({wait_time}s elapsed)")
                time.sleep(check_interval)
                wait_time += check_interval
        
        if wait_time >= max_wait:
            print(f"Warning: Timeout waiting for {filename} after {max_wait} seconds")
        
        # Small additional delay to ensure file is fully written
        time.sleep(1)
        print(video_path_source)
        video_items = resolve.GetMediaStorage().AddItemListToMediaPool(video_path_source)
        for item in video_items:
            print(item.GetName())
        if not video_items:
            print("No media found in import directory.")
            continue

        # Apply Fusion comp to video clip
        timeline = project.GetCurrentTimeline()


        # If no timeline exists, create one
        if not timeline:
            print("No timeline found, creating a new one...")
            
            # Create a new timeline with default settings
            timeline_name = "Timeline 1"
            timeline = mediapool.CreateEmptyTimeline(timeline_name)
            
            if not timeline:
                print("Failed to create timeline")
                exit()
            
            # Set it as the current timeline
            project.SetCurrentTimeline(timeline)
            print(f"Created new timeline: {timeline_name}")
        
        # Clear all clips before adding - with error handling for possible None return
        for track_type in ["video", "audio"]:
            try:
                track_count = timeline.GetTrackCount(track_type)
                if track_count is None:
                    print(f"Warning: Could not get {track_type} track count, skipping cleanup")
                    continue
                    
                for track in range(1, track_count + 1):
                    clips_to_remove = timeline.GetItemListInTrack(track_type, track)
                    if clips_to_remove:
                        if isinstance(clips_to_remove, list):
                            timeline.DeleteClips(clips_to_remove, True)
                        else:
                            timeline.DeleteClips(list(clips_to_remove.values()), True)
            except TypeError as e:
                print(f"Error cleaning {track_type} tracks: {e}")
                print("Attempting to continue processing...")

        clip_info_video = {
            "mediaPoolItem": video_items[0],
            "trackIndex": 1,
            "startFrame": 0,
        }
        mediapool.AppendToTimeline([clip_info_video])

        clips = timeline.GetItemListInTrack("video", 1)
        for item in clips:
            # Use landscape_setting_path for landscape videos, and setting_path for portrait videos
            comp_path = landscape_setting_path if orientation == "landscape" else setting_path
            success = item.ImportFusionComp(comp_path)
            if not success:
                print(f"Failed to import Fusion comp into clip: {filename}")

        fusion_comp = item.LoadFusionCompByName("Composition1")
        
        render_settings = {
            "TargetDir": str(export_dir),
            "Format": "MP4",
            "Codec": "h264",
            "CustomName": str(base_name)
        }
        print(base_name)
        project.SetRenderSettings(render_settings)
        project.AddRenderJob()
        project.StartRendering()

        render_start_time = time.time()
        last_check_time = time.time()
        crash_check_interval = 60  # Check if Resolve crashed every minute

        while project.IsRenderingInProgress():
            print("Rendering in progress for", filename, "...")
            time.sleep(5)
            
            # Every minute, check if DaVinci Resolve is still running
            current_time = time.time()
            if current_time - last_check_time >= crash_check_interval:
                last_check_time = current_time
                # Check if Resolve is still available
                if GetResolve() is None:
                    print("DaVinci Resolve appears to have crashed during rendering!")
                    
                    # Create skip file for crash
                    create_skip_file(filename, post_dir, "DaVinci Resolve crashed during rendering")
                    
                    # Try to restart Resolve
                    print("Attempting to restart DaVinci Resolve...")
                    os.system("pkill -9 -f 'DaVinci Resolve'")
                    time.sleep(10)
                    open_davinci_minimized()
                    time.sleep(15)
                    
                    resolve = get_resolve_with_retry()
                    if not resolve:
                        print("Failed to restart DaVinci Resolve. Exiting.")
                        return
                        
                    # Re-open project and break the rendering loop
                    projectManager = resolve.GetProjectManager()
                    project = projectManager.LoadProject(project_name)
                    if not project:
                        print(f"Unable to re-open project '{project_name}' after crash. Exiting.")
                        return
                    
                    print("DaVinci Resolve recovered after crash. Continuing with next file.")
                    break
                
                # Also abort if rendering has been going for too long (e.g., 30 minutes)
                if current_time - render_start_time > 3600:  # 30 minutes
                    print("Rendering taking too long, possibly stuck. Aborting.")
                    # Create skip file for timeout
                    create_skip_file(filename, post_dir, "Rendering timeout - took longer than 60 minutes")
                    break
                    
        print(f"Rendering finished for {filename}.")

        # Find rendered file - DaVinci may add suffix like _02418924 to filename
        rendered_file = None
        for f in os.listdir(export_dir):
            if f.startswith(base_name) and f.lower().endswith('.mp4'):
                rendered_file = export_dir / f
                # Clean up DaVinci's auto-appended suffixes
                clean_name = clean_rendered_filename(f)
                if clean_name != f:
                    print(f"Found rendered file: {f} -> cleaned to {clean_name}")
                else:
                    print(f"Found rendered file: {f}")
                break

        if rendered_file and rendered_file.exists():
            # Use cleaned filename for destination
            clean_name = clean_rendered_filename(rendered_file.name)
            dest_render = post_dir / clean_name

            if not dest_render.exists():
                try:
                    rsync_copy(rendered_file, dest_render)
                    minimize_davinci()  # Minimize DaVinci Resolve after copying file
                    # Delete source file after successful copy to mimic move behavior
                    os.remove(rendered_file)
                    print(f"Successfully moved {rendered_file.name} to post directory as {clean_name}")

                    # Update API with correct file_type based on first character
                    api_client = VideoAPIClient('https://signcollect.nl/renderServer')
                    first_char = clean_name[0].upper()
                    if first_char == 'L':
                        file_type = 'l_file'
                    elif first_char == 'R':
                        file_type = 'r_file'
                    else:
                        file_type = 'm_file'

                    try:
                        api_client.update_rendered(clean_name, file_type)
                        print(f"Updated API for {clean_name} ({file_type})")
                    except Exception as api_err:
                        print(f"Warning: Failed to update API for {clean_name}: {api_err}")

                except OSError as e:
                    print(f"Failed to move {rendered_file.name} to post directory: {e}")
            else:
                print(f"Rendered file {clean_name} already exists in post directory.")
        else:
            print(f"Rendered file starting with {base_name} not found in export directory.")

        if temp_import_path.exists():
            os.remove(temp_import_path)

        # Clean up import and export directories
        for f in os.listdir(import_dir):
            file_path = import_dir / f
            if file_path.is_file():
                os.remove(file_path)

        for f in os.listdir(export_dir):
            file_path = export_dir / f
            if file_path.is_file():
                os.remove(file_path)

        total_processed += 1
        if total_processed % 50 == 0:
            print(f"Processed {total_processed} videos. Restarting DaVinci Resolve due to memory leak...")
            os.system("pkill -9 -f 'DaVinci Resolve'")
            time.sleep(15)
            open_davinci_minimized()
            time.sleep(15)

    # Send completion heartbeat
    monitor.send_heartbeat_with_stats(
        status='completed',
        message=f'Batch completed: {total_processed} files processed',
        stats={'processed': total_processed, 'total': total_files}
    )
    print("All files processed.")

if __name__ == "__main__":
    # Run once and exit - use batch.sh as watchdog for automatic restart
    # This ensures all file descriptors are released between runs
    exit_code = 0
    try:
        # Register with monitoring system and send heartbeat
        monitor.register()
        monitor.send_heartbeat()

        current_time = datetime.now()
        print(f"Starting batch processing at {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
        main()
        print(f"Batch processing completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Force kill DaVinci Resolve after processing completes
        print("Forcefully terminating DaVinci Resolve to ensure clean state...")
        os.system("pkill -9 -f 'DaVinci Resolve'")

    except Exception as e:
        print(f"ERROR: Main function crashed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Error details: {str(e)}")
        exit_code = 1

        # Force kill DaVinci Resolve after a crash as well
        print("Forcefully terminating DaVinci Resolve after crash...")
        os.system("pkill -9 -f 'DaVinci Resolve'")

    print("Exiting batch.py - watchdog will restart...")
    sys.exit(exit_code)
