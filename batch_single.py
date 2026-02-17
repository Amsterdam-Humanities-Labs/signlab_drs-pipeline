import os, sys, glob, shutil, time, json, subprocess, argparse
from datetime import datetime, timedelta
from python_get_resolve import GetResolve
from pathlib import Path
from video_api_client import VideoAPIClient  # Import the API client

# Ensure output is flushed immediately to logs
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

def handle_mount_failure():
    """Handle mount failure by unmounting the rclone mount"""
    mount_path = "/Users/signlab/signCollect"
    try:
        print(f"Unmounting {mount_path} due to mount failure...")
        result = subprocess.run(['umount', '-f', mount_path],
                              capture_output=True, text=True, timeout=30)
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
                              capture_output=True, text=True, timeout=30)

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
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)

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
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
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
        print(f"Error getting video orientation: {e}")
        return 270, "portrait"

def open_davinci_minimized():
    """Open DaVinci Resolve in minimized mode using AppleScript"""
    cmd = [
        "osascript",
        "-e", 'tell application "DaVinci Resolve" to launch',
        "-e", 'delay 20',
        "-e", 'tell application "System Events" to set visible of process "DaVinci Resolve" to false'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
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
    result = subprocess.run(cmd, capture_output=True, text=True)
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

def main(date_str, pattern):
    homedir = Path("/Users/signlab/")
    base_dir = Path("/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles")
    import_dir = homedir / "drs/import"
    export_dir = homedir / "drs/export"
    setting_path = homedir / "drs/Settings.setting"
    setting_path = str(setting_path)
    landscape_setting_path = homedir / "drs/landscape.setting"
    landscape_setting_path = str(landscape_setting_path)

    print(f"Import dir: {import_dir}")
    print(f"Export dir: {export_dir}")

    # Verify mount health before starting processing
    print("Verifying mount health before processing...")
    if not verify_mount_health():
        print("Mount is unhealthy. Attempting recovery...")
        if trigger_mount_recovery() and wait_for_mount_recovery():
            print("Mount recovery successful. Proceeding with processing.")
        else:
            print("Mount recovery failed. Cannot proceed with processing.")
            return

    # Target single date folder
    raw_dir = base_dir / date_str / "raw"
    post_dir = base_dir / date_str / "post_noncropped"

    if not raw_dir.exists():
        print(f"Raw directory not found: {raw_dir}")
        return

    # Ensure post directory exists
    post_dir.mkdir(parents=True, exist_ok=True)

    date_yyyymmdd = date_str.replace("-", "")

    # Find files to process
    raw_files = list(raw_dir.glob(pattern))
    print(f"Found {len(raw_files)} files matching '{pattern}' in {date_str}/raw")

    files_to_process = []
    for raw_file in raw_files:
        filename = raw_file.name
        dest_post = post_dir / filename

        # Skip if already processed
        if dest_post.exists():
            print(f"Skipping {filename} - already in post_noncropped")
            continue

        # Check date matching
        date_in_filename = filename[1:9]
        if date_in_filename != date_yyyymmdd:
            print(f"Skipping {filename} - date mismatch (expected {date_yyyymmdd}, got {date_in_filename})")
            continue

        # Skip file check disabled - always retry
        # if should_skip_file(filename, post_dir):
        #     print(f"Skipping {filename} - skip file exists")
        #     continue

        files_to_process.append((str(raw_file), date_str, post_dir))

    if not files_to_process:
        print("No new raw files found to process.")
        return

    print(f"Found {len(files_to_process)} files to process. Opening DaVinci Resolve...")

    # Open DaVinci Resolve
    resolve = get_resolve_with_retry()
    if not resolve:
        print(f"Failed to open DaVinci Resolve after multiple attempts. Exiting.")
        return

    # Try to get project manager
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
    for raw_file, date_folder, post_dir in files_to_process:
        raw_file_path = Path(raw_file)
        filename = raw_file_path.name

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
                continue

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

                # Also abort if rendering has been going for too long (e.g., 60 minutes)
                if current_time - render_start_time > 3600:
                    print("Rendering taking too long, possibly stuck. Aborting.")
                    # Create skip file for timeout
                    create_skip_file(filename, post_dir, "Rendering timeout - took longer than 60 minutes")
                    break

        print(f"Rendering finished for {filename}.")

        api_client = VideoAPIClient('https://signcollect.nl/renderServer')

        # Determine file type from filename
        first_char = filename[0].upper()
        if first_char == 'L':
            file_type = 'l_file'
        elif first_char == 'R':
            file_type = 'r_file'
        else:
            file_type = 'm_file'

        api_client.update_rendered(filename, file_type)

        # DaVinci may add extra characters to filename (e.g., L20241209_0279_02340918.mp4)
        # Search for files matching the base name pattern
        rendered_files = list(export_dir.glob(f"{base_name}*.mp4")) + list(export_dir.glob(f"{base_name}*.MP4"))

        if rendered_files:
            # Use the first matching file
            rendered_file = rendered_files[0]
            print(f"Found rendered file: {rendered_file.name}")

            # Always use the clean filename for destination
            dest_render = post_dir / f"{base_name}.MP4"
            if not dest_render.exists():
                try:
                    rsync_copy(rendered_file, dest_render)
                    minimize_davinci()  # Minimize DaVinci Resolve after copying file
                    # Delete source file after successful copy to mimic move behavior
                    os.remove(rendered_file)
                    print(f"Successfully moved {rendered_file.name} -> {base_name}.MP4 to post_noncropped directory")
                except OSError as e:
                    print(f"Failed to move {base_name}.MP4 to post directory: {e}")
                    # Create skip file for failed file operations
                    create_skip_file(filename, post_dir, f"File move failed: {e}")
            else:
                print(f"Rendered file {base_name}.MP4 already exists in post directory.")
        else:
            print(f"Rendered file {base_name}*.mp4 not found in export directory.")
            # List what's actually in export dir for debugging
            export_contents = list(export_dir.glob("*"))
            print(f"Export directory contents: {[f.name for f in export_contents]}")
            # Create skip file for failed rendering
            create_skip_file(filename, post_dir, "Render failed - MP4 not found in export directory")

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

    print(f"All files processed. Total: {total_processed}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Process a specific date folder through DaVinci Resolve (one file at a time)',
        epilog='Example: python batch_single.py --date 2024-12-09'
    )
    parser.add_argument('-d', '--date', required=True,
                        help='Date folder to process (YYYY-MM-DD format)')
    parser.add_argument('-p', '--pattern', default='[LMR]202*.MP4',
                        help='File pattern to match (default: [LMR]202*.MP4)')
    args = parser.parse_args()

    print(f"=== Batch Single Directory Script Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"Target date: {args.date}")
    print(f"Pattern: {args.pattern}")

    # Kill any existing DaVinci Resolve process for clean state
    print("Killing any existing DaVinci Resolve process...")
    os.system("pkill -9 -f 'DaVinci Resolve'")
    time.sleep(3)

    try:
        main(args.date, args.pattern)
    except Exception as e:
        print(f"ERROR: Main function crashed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Error details: {str(e)}")
    finally:
        # Force kill DaVinci Resolve after processing completes
        print("Forcefully terminating DaVinci Resolve...")
        os.system("pkill -9 -f 'DaVinci Resolve'")

    print(f"\n=== Batch Single Directory Script Completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
