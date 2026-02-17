import os, sys, glob, shutil, time, json, subprocess, re, requests
from datetime import datetime
from python_get_resolve import GetResolve
from pathlib import Path
from video_api_client import VideoAPIClient
from signcollect_monitor import SignCollectMonitor
from drs_render_client import DRSRenderClient

# Ensure output is flushed immediately to logs
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Initialize the monitor
monitor = SignCollectMonitor(
    client_id='drs-batch-queue-api',
    client_name='DRS Batch Queue API',
    description='DaVinci Resolve batch queue - API-driven processing',
    heartbeat_interval=3600
)

# Initialize render coordination client to avoid double rendering
MACHINE_NAME = "mac-studio"
render_client = DRSRenderClient(machine_name=MACHINE_NAME)

# Track actively claimed files for crash cleanup
active_claims = []

# Path constants
BASE_DIR = Path("/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles")
export_dir = Path("/Users/signlab/drs/export")
setting_path = "/Users/signlab/drs/Settings.setting"

# Batch limit - 50 files at a time
BATCH_LIMIT = 50

API_BASE_URL = "https://api.signcollect.nl/list/zin/videos"

# ============================================================================
# Helper functions (from batch_queue.py)
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

def clean_rendered_filename(filename):
    """
    Clean up DaVinci Resolve's auto-appended suffixes from filename.
    Example: 'L20241217_1030_1.mp4' -> 'L20241217_1030.MP4'
    """
    match = re.match(r'^([LMR]\d{8}_\d{4})(?:_\d+| \d+)?\.mp4$', filename, re.IGNORECASE)
    if match:
        base_name = match.group(1)
        return f"{base_name}.MP4"
    return filename

# ============================================================================
# API-driven file discovery
# ============================================================================

def fetch_all_video_filenames():
    """Fetch all video filenames from the signcollect API (all pages)."""
    filenames = set()
    page = 1

    print("Fetching video list from API...")

    while True:
        url = f"{API_BASE_URL}?page={page}&nofilter=1"
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            print(f"Error fetching page {page}: {e}")
            break

        entries = data.get("data", [])
        if not entries:
            break

        for entry in entries:
            for video_obj in entry.get("videos", []):
                for angle in ["left", "center", "right"]:
                    video_url = video_obj.get(angle)
                    if video_url:
                        # Extract filename from URL: https://media.signcollect.nl/L20250522_9305.mp4
                        fname = video_url.rsplit("/", 1)[-1]
                        filenames.add(fname)

        meta = data.get("meta", {})
        total_pages = meta.get("total_pages", 1)
        print(f"  Page {page}/{total_pages} - collected {len(filenames)} unique filenames so far")

        if page >= total_pages:
            break
        page += 1

    print(f"Total unique video filenames from API: {len(filenames)}")
    return filenames

def extract_date_from_filename(filename):
    """
    Extract date from filename like L20250522_9305.mp4 -> '2025-05-22'
    Returns None if pattern doesn't match.
    """
    match = re.match(r'^[LMR](\d{4})(\d{2})(\d{2})_\d+\.mp4$', filename, re.IGNORECASE)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return None

def get_unique_dates_from_api(filenames):
    """Extract unique date folders from a set of API filenames."""
    dates = set()
    for fname in filenames:
        date_str = extract_date_from_filename(fname)
        if date_str:
            dates.add(date_str)
    return sorted(dates)

def get_api_driven_raw_files(api_filenames):
    """
    For each unique date in the API filenames, look in the corresponding
    raw folder for files that need processing.
    """
    dates = get_unique_dates_from_api(api_filenames)
    print(f"Found {len(dates)} unique dates in API data")

    # Build a set of base names from the API (uppercase, no extension) for fast lookup
    api_basenames = set()
    for fname in api_filenames:
        # Normalize: L20250522_9305.mp4 -> L20250522_9305
        base = os.path.splitext(fname)[0].upper()
        api_basenames.add(base)

    all_files = []

    for date_str in dates:
        raw_dir = BASE_DIR / date_str / "raw"
        post_noncropped_dir = BASE_DIR / date_str / "post_noncropped"

        if not raw_dir.exists():
            continue

        # Find raw files that match API filenames
        raw_files = []
        for raw_file in raw_dir.glob("[LMR]202*.MP4"):
            raw_basename = raw_file.stem.upper()  # e.g. L20250522_9305
            if raw_basename in api_basenames:
                raw_files.append(raw_file)

        if raw_files:
            date_yyyymmdd = date_str.replace("-", "")
            print(f"  {date_str}: {len(raw_files)} raw files match API")
            all_files.append((raw_files, post_noncropped_dir, date_yyyymmdd))

    return all_files

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

        # Validate date in filename matches expected date
        date_in_filename = filename[1:9]
        if date_in_filename != expected_date:
            print(f"Skipping {filename} - date mismatch (expected {expected_date}, got {date_in_filename})")
            continue

        to_process.append(raw_file)

    return to_process

# ============================================================================
# DaVinci Resolve processing (same as batch_queue.py)
# ============================================================================

def queue_files_for_project(resolve, projectManager, project_name, files, setting_path, post_noncropped_dir):
    """Add all files to render queue for a specific project"""

    project = projectManager.LoadProject(project_name)
    if not project:
        print(f"Failed to load project: {project_name}")
        return None

    project.DeleteAllRenderJobs()

    mediapool = project.GetMediaPool()

    root_folder = mediapool.GetRootFolder()
    all_clips = get_all_clips(root_folder, [])
    if all_clips:
        mediapool.DeleteClips(all_clips)

    print(f"Adding {len(files)} files to media pool...")
    file_paths = [str(f) for f in files]
    all_video_items = resolve.GetMediaStorage().AddItemListToMediaPool(file_paths)

    if not all_video_items:
        print("Failed to add any files to media pool")
        return None

    print(f"Added {len(all_video_items)} files to media pool")

    clip_map = {}
    for item in all_video_items:
        clip_map[item.GetName()] = item

    for raw_file in files:
        filename = raw_file.name
        base_name = raw_file.stem

        print(f"Queuing {filename} for project {project_name}...")

        video_item = clip_map.get(filename)
        if not video_item:
            print(f"Failed to find {filename} in media pool")
            create_skip_file(filename, post_noncropped_dir, "Failed to find in media pool")
            continue

        timeline_name = f"Timeline_{base_name}"
        timeline = mediapool.CreateEmptyTimeline(timeline_name)
        if not timeline:
            print(f"Failed to create timeline for {filename}")
            create_skip_file(filename, post_noncropped_dir, "Failed to create timeline")
            continue
        project.SetCurrentTimeline(timeline)

        clip_info = {
            "mediaPoolItem": video_item,
            "trackIndex": 1,
            "startFrame": 0,
        }
        mediapool.AppendToTimeline([clip_info])

        clips = timeline.GetItemListInTrack("video", 1)
        for item in clips:
            success = item.ImportFusionComp(setting_path)
            if not success:
                print(f"Failed to import Fusion comp for {filename}")
            item.LoadFusionCompByName("Composition1")

        project.SetCurrentTimeline(timeline)

        render_settings = {
            "TargetDir": str(export_dir),
            "Format": "MP4",
            "Codec": "h264",
            "CustomName": base_name,
            "UniqueFilenameStyle": 0
        }
        project.SetRenderSettings(render_settings)

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

    minimize_davinci()

    projectManager = resolve.GetProjectManager()

    project = queue_files_for_project(resolve, projectManager, "lala6",
                                      files_batch, setting_path, post_noncropped_dir)

    if not project:
        print("Failed to queue files - project not loaded")
        return False

    render_jobs = project.GetRenderJobList()
    job_count = len(render_jobs) if render_jobs else 0
    print(f"Starting render of {job_count} queued jobs...")
    minimize_davinci()
    project.StartRendering()

    while project.IsRenderingInProgress():
        print(f"Rendering in progress... ({datetime.now().strftime('%H:%M:%S')})")
        time.sleep(10)

    print("Batch render complete!")

    # Move rendered files to post_noncropped
    print("Moving rendered files to post_noncropped...")
    api_client = VideoAPIClient('https://signcollect.nl/renderServer')
    completed_files = []
    failed_files = []

    for rendered_file in export_dir.glob("*.mp4"):
        original_filename = rendered_file.name
        clean_filename = clean_rendered_filename(original_filename)

        if clean_filename != original_filename:
            print(f"  Cleaning filename: {original_filename} -> {clean_filename}")

        date_yyyymmdd = clean_filename[1:9]
        date_formatted = f"{date_yyyymmdd[:4]}-{date_yyyymmdd[4:6]}-{date_yyyymmdd[6:8]}"
        target_post_dir = BASE_DIR / date_formatted / "post_noncropped"
        target_post_dir.mkdir(parents=True, exist_ok=True)

        dest = target_post_dir / clean_filename
        print(f"  Moving {clean_filename} to {date_formatted}/post_noncropped/...")
        try:
            rsync_copy(rendered_file, dest)
            rendered_file.unlink()
            print(f"  Moved {clean_filename} to post_noncropped")
            completed_files.append(clean_filename)

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
            failed_files.append(clean_filename)

    # Mark completed files via render coordination API
    if completed_files:
        render_client.complete_files(completed_files)
        print(f"  Marked {len(completed_files)} files as completed via render API")
        for f in completed_files:
            if f in active_claims:
                active_claims.remove(f)

    # Release failed files so other machines can retry
    if failed_files:
        render_client.release_files(failed_files)
        print(f"  Released {len(failed_files)} failed files back to pool")
        for f in failed_files:
            if f in active_claims:
                active_claims.remove(f)

    # Force quit DaVinci Resolve to free memory
    print("Closing DaVinci Resolve to free memory...")
    os.system("pkill -9 -f 'DaVinci Resolve'")
    time.sleep(5)

    return True

# ============================================================================
# Main
# ============================================================================

def main():
    print(f"=== Batch Queue API Script Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

    # Kill any existing DaVinci Resolve process for clean state
    print("Killing any existing DaVinci Resolve process...")
    os.system("pkill -9 -f 'DaVinci Resolve'")
    time.sleep(3)

    # Clean up stale claims from previous crashed runs
    print("Cleaning up stale render claims (>60 min old)...")
    render_client.cleanup_stale(max_age_minutes=60)

    # Verify mount health
    if not verify_mount_health():
        print("Mount is not healthy. Exiting.")
        return

    # 1. Fetch all video filenames from the API
    api_filenames = fetch_all_video_filenames()
    if not api_filenames:
        print("No video filenames fetched from API.")
        return

    # 2. Find raw files on disk that match API filenames
    recent_files = get_api_driven_raw_files(api_filenames)

    if not recent_files:
        print("No matching raw files found on disk.")
        return

    # 3. Collect all files to process across all dates
    all_files_to_process = []
    all_post_dirs = {}

    for raw_files, post_noncropped_dir, expected_date in recent_files:
        files_to_process = get_files_to_process(raw_files, post_noncropped_dir, expected_date)

        if files_to_process:
            for f in files_to_process:
                all_post_dirs[str(f)] = post_noncropped_dir
            all_files_to_process.extend(files_to_process)
            post_noncropped_dir.mkdir(parents=True, exist_ok=True)

    if not all_files_to_process:
        print("No files to process (all already processed or skipped).")
        return

    print(f"Found {len(all_files_to_process)} total files to process across all dates")

    # 4. Split files into batches of 50
    batches = [all_files_to_process[i:i + BATCH_LIMIT] for i in range(0, len(all_files_to_process), BATCH_LIMIT)]
    total_batches = len(batches)

    print(f"Split into {total_batches} batch(es) of up to {BATCH_LIMIT} files each")

    # 5. Process each batch with render coordination
    for batch_num, batch in enumerate(batches, 1):
        # Claim this batch's files via render coordination API
        batch_filenames = [f.name for f in batch]
        claimed_filenames = render_client.claim_files(batch_filenames)
        claimed_set = set(claimed_filenames)

        # Filter batch to only files we successfully claimed
        claimed_batch = [f for f in batch if f.name in claimed_set]
        skipped = len(batch) - len(claimed_batch)

        if skipped > 0:
            print(f"  Skipped {skipped} files already claimed by another machine")

        if not claimed_batch:
            print(f"  Batch {batch_num}: all files already claimed by other machines, skipping")
            continue

        # Track active claims for crash cleanup
        active_claims.extend(claimed_filenames)

        print(f"  Batch {batch_num}: claimed {len(claimed_batch)}/{len(batch)} files")

        first_file = claimed_batch[0]
        post_noncropped_dir = all_post_dirs[str(first_file)]
        success = process_batch(claimed_batch, post_noncropped_dir, batch_num, total_batches)
        if not success:
            # Release all claims from this batch on failure
            render_client.release_files(claimed_filenames)
            print(f"  Released {len(claimed_filenames)} claims after batch failure")
            for f in claimed_filenames:
                if f in active_claims:
                    active_claims.remove(f)
            print(f"Batch {batch_num} failed. Stopping.")
            break

    print(f"\n=== Batch Queue API Script Completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

if __name__ == "__main__":
    monitor.register()

    print("Starting batch_queue_api service - will check for files every hour")
    while True:
        try:
            monitor.send_heartbeat()
            main()
        except KeyboardInterrupt:
            print("\nService stopped by user")
            # Release any active claims on shutdown
            if active_claims:
                render_client.release_files(active_claims)
                print(f"Released {len(active_claims)} active claims on shutdown")
                active_claims.clear()
            break
        except Exception as e:
            print(f"Unexpected error: {e}")
            # Release any active claims on crash
            if active_claims:
                render_client.release_files(active_claims)
                print(f"Released {len(active_claims)} active claims after crash")
                active_claims.clear()
            print("Sleeping for 1 hour before retry...")
            time.sleep(60 * 60)
