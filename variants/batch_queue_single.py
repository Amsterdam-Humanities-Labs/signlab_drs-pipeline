import os, sys, glob, shutil, time, json, subprocess, argparse
from datetime import datetime
sys.path.insert(0, '/Users/signlab/drs/services')
sys.path.insert(0, '/Users/signlab/drs/shared')
from python_get_resolve import GetResolve
from pathlib import Path
from video_api_client import VideoAPIClient  # Import the API client

# Ensure output is flushed immediately to logs
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Path constants
BASE_DIR = Path("/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles")
export_dir = Path("/Users/signlab/drs/export")
setting_path = "/Users/signlab/drs/config/Settings.setting"  # lala6 project

# Batch limit to prevent DaVinci memory issues
BATCH_LIMIT = 150

# ============================================================================
# Helper functions (from batch.py)
# ============================================================================

def verify_mount_health():
    """Verify that the rclone mount is healthy and accessible"""
    mount_path = "/Users/signlab/signCollect"
    test_path = os.path.join(mount_path, "AIHR-FGW-TEST-SIGNLAB (Projectfolder)")

    try:
        if not os.path.exists(mount_path):
            print(f"Mount path does not exist: {mount_path}")
            return False

        if not os.path.exists(test_path):
            print(f"Expected directory not found in mount: {test_path}")
            return False

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
    """Use rsync to copy files from rclone mounted drives for better performance."""
    dest_path = Path(destination)
    dest_dir = dest_path.parent
    if not dest_dir.exists():
        os.makedirs(dest_dir, exist_ok=True)

    for attempt in range(retries):
        try:
            cmd = ["rsync", "-av", "--progress", str(source), str(destination)]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)

            if os.path.exists(destination) and os.path.getsize(destination) == 0:
                if os.path.exists(source) and os.path.getsize(source) > 0:
                    print(f"WARNING: Copied file {destination} is 0KB but source is {os.path.getsize(source)} bytes")
                    raise OSError("Mount failure detected - destination file is 0KB")

            return True

        except subprocess.CalledProcessError as e:
            error_msg = f"rsync failed for {source} to {destination}"
            if e.stderr:
                error_msg += f": {e.stderr}"

            print(f"Attempt {attempt + 1}/{retries} - {error_msg}")

            if attempt < retries - 1:
                print(f"Retrying in 2 seconds...")
                time.sleep(2)
            else:
                print(f"All attempts failed for {source}")
                raise OSError(f"rsync failed after {retries} attempts: {error_msg}")

    return False

def get_video_orientation(filepath):
    """Get video orientation using mediainfo"""
    if not os.path.isfile(filepath):
        print(f"File not found: {filepath}")
        return None, "file_not_found"

    cmd = ["/opt/homebrew/bin/mediainfo", "--Output=JSON", filepath]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
    data = json.loads(result.stdout)

    try:
        video_track = next(track for track in data["media"]["track"] if track["@type"] == "Video")
        width = int(video_track.get("Width", 0))
        height = int(video_track.get("Height", 0))
        rotation = int(float(video_track.get("Rotation", 0)))

        if rotation in [90, 270]:
            width, height = height, width

        orientation = "portrait" if height > width else "landscape"
        return rotation, orientation
    except Exception as e:
        print(f"Error getting video orientation: {e}")
        return 270, "portrait"

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

def get_all_clips(folder, clips_list):
    """Get all clips from all folders in media pool"""
    clips_list.extend(folder.GetClipList())
    for subfolder in folder.GetSubFolderList():
        get_all_clips(subfolder, clips_list)
    return clips_list

import re

def clean_rendered_filename(filename):
    """
    Clean up DaVinci Resolve's auto-appended suffixes from filename.
    DaVinci may add '_1', '_2', ' 1', ' 2' etc. before the extension.
    Example: 'L20241217_1030_1.mp4' -> 'L20241217_1030.MP4'
    Example: 'M20241217_1030 1.mp4' -> 'M20241217_1030.MP4'
    """
    # Pattern to match: base_name + optional suffix (_N or space N) + extension
    # Original filename pattern: [L|M|R]YYYYMMDD_HHMM.MP4
    match = re.match(r'^([LMR]\d{8}_\d{4})(?:_\d+| \d+)?\.mp4$', filename, re.IGNORECASE)
    if match:
        base_name = match.group(1)
        return f"{base_name}.MP4"  # Return with original .MP4 extension
    return filename  # Return unchanged if pattern doesn't match

# ============================================================================
# Single Directory Functions
# ============================================================================

def get_raw_files_from_dir(date_str, pattern="[LMR]202*.MP4"):
    """Get raw files from a specific date folder"""
    raw_dir = BASE_DIR / date_str / "raw"
    post_noncropped_dir = BASE_DIR / date_str / "post_noncropped"
    date_yyyymmdd = date_str.replace("-", "")

    if not raw_dir.exists():
        print(f"Raw directory not found: {raw_dir}")
        return None

    raw_files = list(raw_dir.glob(pattern))
    print(f"Found {len(raw_files)} raw files matching '{pattern}' in {date_str}")
    return (raw_files, post_noncropped_dir, date_yyyymmdd)

def get_files_to_process(raw_files, post_noncropped_dir, expected_date):
    """Filter out files that already exist in post_noncropped"""
    to_process = []

    for raw_file in raw_files:
        filename = raw_file.name
        output_path = post_noncropped_dir / filename

        # Skip if output already exists
        if output_path.exists():
            print(f"Skipping {filename} - already in post_noncropped")
            continue

        # Skip if .skip file exists
        if should_skip_file(filename, post_noncropped_dir):
            print(f"Skipping {filename} - skip file exists")
            continue

        # Validate date in filename matches expected date
        date_in_filename = filename[1:9]  # Extract YYYYMMDD from filename
        if date_in_filename != expected_date:
            print(f"Skipping {filename} - date mismatch (expected {expected_date}, got {date_in_filename})")
            continue

        to_process.append(raw_file)

    return to_process

def queue_files_for_project(resolve, projectManager, project_name, files, setting_path, post_noncropped_dir):
    """Add all files to render queue for a specific project (uses source files directly)"""

    # Load the project
    project = projectManager.LoadProject(project_name)
    if not project:
        print(f"Failed to load project: {project_name}")
        return None

    # Clear existing render jobs
    project.DeleteAllRenderJobs()

    mediapool = project.GetMediaPool()

    # Clear media pool once at the start
    root_folder = mediapool.GetRootFolder()
    all_clips = get_all_clips(root_folder, [])
    if all_clips:
        mediapool.DeleteClips(all_clips)

    # Add ALL files to media pool at once (faster than one-by-one)
    print(f"Adding {len(files)} files to media pool...")
    file_paths = [str(f) for f in files]
    all_video_items = resolve.GetMediaStorage().AddItemListToMediaPool(file_paths)

    if not all_video_items:
        print("Failed to add any files to media pool")
        return None

    print(f"Added {len(all_video_items)} files to media pool")

    # Create a mapping of filename to media pool item
    clip_map = {}
    for item in all_video_items:
        clip_map[item.GetName()] = item

    for raw_file in files:
        filename = raw_file.name
        base_name = raw_file.stem  # filename without extension

        print(f"Queuing {filename} for project {project_name}...")

        # Get the media pool item for this file
        video_item = clip_map.get(filename)
        if not video_item:
            print(f"Failed to find {filename} in media pool")
            create_skip_file(filename, post_noncropped_dir, "Failed to find in media pool")
            continue

        # Create a new timeline for this video
        timeline_name = f"Timeline_{base_name}"
        timeline = mediapool.CreateEmptyTimeline(timeline_name)
        if not timeline:
            print(f"Failed to create timeline for {filename}")
            create_skip_file(filename, post_noncropped_dir, "Failed to create timeline")
            continue
        project.SetCurrentTimeline(timeline)

        # Add video to timeline
        clip_info = {
            "mediaPoolItem": video_item,
            "trackIndex": 1,
            "startFrame": 0,
        }
        mediapool.AppendToTimeline([clip_info])

        # Apply Fusion composition
        clips = timeline.GetItemListInTrack("video", 1)
        for item in clips:
            success = item.ImportFusionComp(setting_path)
            if not success:
                print(f"Failed to import Fusion comp for {filename}")
            item.LoadFusionCompByName("Composition1")

        # Ensure this timeline is current before adding render job
        project.SetCurrentTimeline(timeline)

        # Configure render settings for this specific file
        render_settings = {
            "TargetDir": str(export_dir),
            "Format": "MP4",
            "Codec": "h264",
            "CustomName": base_name,
            "UniqueFilenameStyle": 0  # Don't append unique suffix
        }
        project.SetRenderSettings(render_settings)

        # Add to render queue (but don't start yet!)
        job_id = project.AddRenderJob()
        if job_id:
            print(f"Added {filename} to render queue (job ID: {job_id})")
        else:
            print(f"WARNING: Failed to add {filename} to render queue")

    print(f"Queued {len(files)} files for project {project_name}")
    return project

def process_batch(files_batch, post_noncropped_dir, batch_num, total_batches):
    """Process a single batch of files"""
    print(f"\n=== Processing batch {batch_num}/{total_batches} ({len(files_batch)} files) ===")

    # Clean export directory
    print("Cleaning export directory...")
    for f in export_dir.glob("*"):
        if f.is_file():
            f.unlink()

    # Open DaVinci Resolve
    print("Opening DaVinci Resolve...")
    resolve = get_resolve_with_retry()
    if not resolve:
        print("Failed to open DaVinci Resolve")
        return False

    # Hide DaVinci Resolve window
    minimize_davinci()

    projectManager = resolve.GetProjectManager()

    # Queue files using lala6 project (directly from source, no copying)
    project = queue_files_for_project(resolve, projectManager, "lala6",
                                      files_batch, setting_path, post_noncropped_dir)

    if not project:
        print("Failed to queue files - project not loaded")
        return False

    # Start rendering all queued jobs
    render_jobs = project.GetRenderJobList()
    job_count = len(render_jobs) if render_jobs else 0
    print(f"Starting render of {job_count} queued jobs...")
    minimize_davinci()  # Keep hidden during render
    project.StartRendering()

    # Monitor until complete
    while project.IsRenderingInProgress():
        print(f"Rendering in progress... ({datetime.now().strftime('%H:%M:%S')})")
        time.sleep(10)

    print("Batch render complete!")

    # Move rendered files to post_noncropped (determine correct directory from filename)
    print("Moving rendered files to post_noncropped...")
    api_client = VideoAPIClient('https://signcollect.nl/renderServer')

    for rendered_file in export_dir.glob("*.mp4"):
        original_filename = rendered_file.name
        # Clean up DaVinci's auto-appended suffixes (e.g., _1, _2, etc.)
        clean_filename = clean_rendered_filename(original_filename)

        if clean_filename != original_filename:
            print(f"  Cleaning filename: {original_filename} -> {clean_filename}")

        # Extract date from cleaned filename pattern [L|M|R]YYYYMMDD_HHmm.MP4
        date_yyyymmdd = clean_filename[1:9]  # Extract YYYYMMDD
        date_formatted = f"{date_yyyymmdd[:4]}-{date_yyyymmdd[4:6]}-{date_yyyymmdd[6:8]}"  # YYYY-MM-DD
        target_post_dir = BASE_DIR / date_formatted / "post_noncropped"
        target_post_dir.mkdir(parents=True, exist_ok=True)

        dest = target_post_dir / clean_filename  # Use cleaned filename for destination
        print(f"  Moving {clean_filename} to {date_formatted}/post_noncropped/...")
        try:
            rsync_copy(rendered_file, dest)
            rendered_file.unlink()
            print(f"  Moved {clean_filename} to post_noncropped")

            # Upload to signcollect.nl API - determine file type from filename
            # Filename pattern: [L|M|R]YYYYMMDD_HHmm.MP4
            try:
                first_char = clean_filename[0].upper()
                if first_char == 'L':
                    file_type = 'l_file'
                elif first_char == 'R':
                    file_type = 'r_file'
                else:
                    file_type = 'm_file'

                api_client.update_rendered(clean_filename, file_type)
                print(f"  Updated API for {clean_filename} ({file_type})")
            except Exception as api_err:
                print(f"  Warning: Failed to update API for {clean_filename}: {api_err}")

        except OSError as e:
            print(f"  Failed to move {clean_filename}: {e}")

    # Force quit DaVinci Resolve to free memory
    print("Closing DaVinci Resolve to free memory...")
    os.system("pkill -9 -f 'DaVinci Resolve'")
    time.sleep(5)  # Wait for clean shutdown

    return True

def main():
    parser = argparse.ArgumentParser(
        description='Process a specific date folder through DaVinci Resolve',
        epilog='Example: python batch_queue_single.py --date 2024-12-09'
    )
    parser.add_argument('-d', '--date', required=True,
                        help='Date folder to process (YYYY-MM-DD format)')
    parser.add_argument('-p', '--pattern', default='[LMR]202*.MP4',
                        help='File pattern to match (default: [LMR]202*.MP4)')
    args = parser.parse_args()

    print(f"=== Batch Queue Single Directory Script Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"Target date: {args.date}")
    print(f"Pattern: {args.pattern}")

    # Kill any existing DaVinci Resolve process for clean state
    print("Killing any existing DaVinci Resolve process...")
    os.system("pkill -9 -f 'DaVinci Resolve'")
    time.sleep(3)

    # Verify mount health
    if not verify_mount_health():
        print("Mount is not healthy. Exiting.")
        return

    # Get files from the specified date
    result = get_raw_files_from_dir(args.date, args.pattern)

    if not result:
        print(f"No files found for {args.date}")
        return

    raw_files, post_noncropped_dir, expected_date = result

    # Filter to files that need processing
    files_to_process = get_files_to_process(raw_files, post_noncropped_dir, expected_date)

    if not files_to_process:
        print("No files to process (all already processed or skipped).")
        return

    print(f"Found {len(files_to_process)} files to process")

    # Ensure post_noncropped directory exists
    post_noncropped_dir.mkdir(parents=True, exist_ok=True)

    # Split files into batches of BATCH_LIMIT to prevent memory issues
    batches = [files_to_process[i:i + BATCH_LIMIT] for i in range(0, len(files_to_process), BATCH_LIMIT)]
    total_batches = len(batches)

    print(f"Split into {total_batches} batch(es) of up to {BATCH_LIMIT} files each")

    # Process each batch
    for batch_num, batch in enumerate(batches, 1):
        success = process_batch(batch, post_noncropped_dir, batch_num, total_batches)
        if not success:
            print(f"Batch {batch_num} failed. Stopping.")
            break

    print(f"\n=== Batch Queue Single Directory Script Completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

if __name__ == "__main__":
    main()
