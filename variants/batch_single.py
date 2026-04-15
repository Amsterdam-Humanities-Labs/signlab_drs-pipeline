import os, sys, time, json, subprocess, re, gc
from datetime import datetime
sys.path.insert(0, '/Users/signlab/drs/services')
sys.path.insert(0, '/Users/signlab/drs/shared')
from python_get_resolve import GetResolve
from pathlib import Path
from video_api_client import VideoAPIClient

# Ensure output is flushed immediately to logs
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ---- Configuration ----
TARGET_FILES = [
    "M20260217_4785.MP4",
    "L20260217_6368.MP4",
    "R20260217_9005.MP4",
]
DATE_FOLDER = "2026-02-17"
BASE_DIR = Path("/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles")
RAW_DIR = BASE_DIR / DATE_FOLDER / "raw"
POST_DIR = BASE_DIR / DATE_FOLDER / "post_noncropped"
IMPORT_DIR = Path("/Users/signlab/drs/import")
EXPORT_DIR = Path("/Users/signlab/drs/export")
SETTING_PATH = "/Users/signlab/drs/config/Settings.setting"
LANDSCAPE_SETTING_PATH = "/Users/signlab/drs/config/landscape.setting"


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


def rsync_copy(source, destination, retries=2):
    """Use rsync to copy files."""
    dest_path = Path(destination)
    dest_dir = dest_path.parent
    if not dest_dir.exists():
        os.makedirs(dest_dir, exist_ok=True)

    for attempt in range(retries):
        try:
            cmd = ["rsync", "-av", "--progress", str(source), str(destination)]
            subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)

            if os.path.exists(destination) and os.path.getsize(destination) == 0:
                if os.path.exists(source) and os.path.getsize(source) > 0:
                    print(f"WARNING: Copied file {destination} is 0KB but source is {os.path.getsize(source)} bytes")
                    raise OSError("Destination file is 0KB")

            return True

        except subprocess.CalledProcessError as e:
            error_msg = f"rsync failed for {source} to {destination}"
            if e.stderr:
                error_msg += f": {e.stderr}"
            print(f"Attempt {attempt + 1}/{retries} - {error_msg}")

            if attempt < retries - 1:
                print("Retrying in 2 seconds...")
                time.sleep(2)
            else:
                raise OSError(f"rsync failed after {retries} attempts: {error_msg}")

    return False


def get_video_orientation(filepath):
    if not os.path.isfile(filepath):
        print(f"File not found: {filepath}")
        return None, "file_not_found"

    cmd = ["/opt/homebrew/bin/mediainfo", "--Output=JSON", filepath]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
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


def main():
    gc.collect()

    # Create post_noncropped directory if needed
    if not POST_DIR.exists():
        os.makedirs(POST_DIR)
        print(f"Created output directory: {POST_DIR}")

    # Verify target files exist
    for filename in TARGET_FILES:
        raw_path = RAW_DIR / filename
        if not raw_path.exists():
            print(f"ERROR: Target file not found: {raw_path}")
            return

    print(f"All {len(TARGET_FILES)} target files found in {RAW_DIR}")

    # Clean up import directory before processing
    for f in os.listdir(IMPORT_DIR):
        file_path = IMPORT_DIR / f
        if file_path.is_file():
            os.remove(file_path)

    # Open DaVinci Resolve
    print("Opening DaVinci Resolve...")
    resolve = get_resolve_with_retry()
    if not resolve:
        print("Failed to open DaVinci Resolve after multiple attempts. Exiting.")
        return

    try:
        projectManager = resolve.GetProjectManager()
    except AttributeError:
        print("Lost connection to DaVinci Resolve. Attempting to reopen...")
        resolve = get_resolve_with_retry()
        if not resolve:
            print("Failed to reopen DaVinci Resolve. Exiting.")
            return
        projectManager = resolve.GetProjectManager()

    total_processed = 0
    for filename in TARGET_FILES:
        raw_file = RAW_DIR / filename

        # Check if already processed
        dest_post = POST_DIR / filename
        if dest_post.exists():
            print(f"Already processed: {filename} — skipping.")
            total_processed += 1
            continue

        # Check video orientation
        rotation, orientation = get_video_orientation(str(raw_file))
        if orientation == "file_not_found":
            print(f"File not accessible, skipping: {filename}")
            continue

        # Determine project
        project_name = "landscape" if orientation == "landscape" else "lala6"

        if orientation != "portrait" and filename.startswith("M"):
            print(f"Skipping {filename} - {orientation} orientation detected for M camera")
            continue

        print(f"Processing file: {filename} (orientation: {orientation}, project: {project_name})")
        temp_import_path = IMPORT_DIR / filename
        try:
            rsync_copy(str(raw_file), str(temp_import_path))
            minimize_davinci()
        except OSError as e:
            print(f"rsync copy failed for {filename}. Skipping. Error: {e}")
            continue

        # Connect to DaVinci Resolve
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

            if not project or project.GetName() != project_name:
                print(f"Opening project '{project_name}' for {orientation} video...")
                project = projectManager.LoadProject(project_name)
                if not project:
                    print(f"Unable to open project '{project_name}'. Exiting.")
                    return

        project.DeleteAllRenderJobs()
        base_name = os.path.splitext(filename)[0]

        # Check if output already exists
        expected_output = POST_DIR / f"{base_name}.mp4"
        if expected_output.exists():
            print(f"Rendered file {base_name}.mp4 already exists in post directory. Skipping rendering.")
            if temp_import_path.exists():
                os.remove(temp_import_path)
            continue

        mediapool = project.GetMediaPool()

        # Get all clips from all folders and delete them
        def get_all_clips(folder, clips_list):
            clips_list.extend(folder.GetClipList())
            for subfolder in folder.GetSubFolderList():
                get_all_clips(subfolder, clips_list)
            return clips_list

        root_folder = mediapool.GetRootFolder()
        all_clips = get_all_clips(root_folder, [])

        if all_clips:
            success = mediapool.DeleteClips(all_clips)
            if success:
                print(f"Successfully deleted {len(all_clips)} clips")
            else:
                print("Failed to delete clips")

        video_path_source = str(IMPORT_DIR)

        # Wait for the file to be available
        wait_time = 0
        max_wait = 30
        while wait_time < max_wait:
            if temp_import_path.exists():
                print(f"File {filename} is available in import directory")
                break
            else:
                print(f"Waiting for {filename} to be available... ({wait_time}s elapsed)")
                time.sleep(2)
                wait_time += 2

        if wait_time >= max_wait:
            print(f"Warning: Timeout waiting for {filename} after {max_wait} seconds")

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

        if not timeline:
            print("No timeline found, creating a new one...")
            timeline_name = "Timeline 1"
            timeline = mediapool.CreateEmptyTimeline(timeline_name)
            if not timeline:
                print("Failed to create timeline")
                continue
            project.SetCurrentTimeline(timeline)
            print(f"Created new timeline: {timeline_name}")

        # Clear all clips from timeline
        for track_type in ["video", "audio"]:
            try:
                track_count = timeline.GetTrackCount(track_type)
                if track_count is None:
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

        clip_info_video = {
            "mediaPoolItem": video_items[0],
            "trackIndex": 1,
            "startFrame": 0,
        }
        mediapool.AppendToTimeline([clip_info_video])

        clips = timeline.GetItemListInTrack("video", 1)
        for item in clips:
            comp_path = LANDSCAPE_SETTING_PATH if orientation == "landscape" else SETTING_PATH
            success = item.ImportFusionComp(comp_path)
            if not success:
                print(f"Failed to import Fusion comp into clip: {filename}")

        fusion_comp = item.LoadFusionCompByName("Composition1")

        render_settings = {
            "TargetDir": str(EXPORT_DIR),
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

        while project.IsRenderingInProgress():
            print(f"Rendering in progress for {filename} ...")
            time.sleep(5)

            current_time = time.time()
            if current_time - last_check_time >= 60:
                last_check_time = current_time
                if GetResolve() is None:
                    print("DaVinci Resolve appears to have crashed during rendering!")
                    print("Attempting to restart DaVinci Resolve...")
                    os.system("pkill -9 -f 'DaVinci Resolve'")
                    time.sleep(10)
                    open_davinci_minimized()
                    time.sleep(15)

                    resolve = get_resolve_with_retry()
                    if not resolve:
                        print("Failed to restart DaVinci Resolve. Exiting.")
                        return

                    projectManager = resolve.GetProjectManager()
                    project = projectManager.LoadProject(project_name)
                    if not project:
                        print(f"Unable to re-open project '{project_name}' after crash. Exiting.")
                        return

                    print("DaVinci Resolve recovered after crash. Continuing with next file.")
                    break

                if current_time - render_start_time > 3600:
                    print("Rendering taking too long (>60 min), aborting.")
                    break

        print(f"Rendering finished for {filename}.")

        # Find rendered file
        rendered_file = None
        for f in os.listdir(EXPORT_DIR):
            if f.startswith(base_name) and f.lower().endswith('.mp4'):
                rendered_file = EXPORT_DIR / f
                clean_name = clean_rendered_filename(f)
                if clean_name != f:
                    print(f"Found rendered file: {f} -> cleaned to {clean_name}")
                else:
                    print(f"Found rendered file: {f}")
                break

        if rendered_file and rendered_file.exists():
            clean_name = clean_rendered_filename(rendered_file.name)
            dest_render = POST_DIR / clean_name

            if not dest_render.exists():
                try:
                    rsync_copy(str(rendered_file), str(dest_render))
                    minimize_davinci()
                    os.remove(rendered_file)
                    print(f"Successfully moved {rendered_file.name} to post directory as {clean_name}")

                    # Update API
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

        # Clean up
        if temp_import_path.exists():
            os.remove(temp_import_path)

        for f in os.listdir(IMPORT_DIR):
            file_path = IMPORT_DIR / f
            if file_path.is_file():
                os.remove(file_path)

        for f in os.listdir(EXPORT_DIR):
            file_path = EXPORT_DIR / f
            if file_path.is_file():
                os.remove(file_path)

        total_processed += 1

    print(f"Done. Processed {total_processed}/{len(TARGET_FILES)} files.")


if __name__ == "__main__":
    exit_code = 0
    try:
        print(f"=== batch_single.py started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
        print(f"Target files: {TARGET_FILES}")
        print(f"Date folder: {DATE_FOLDER}")
        main()
        print(f"Processing completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        print("Forcefully terminating DaVinci Resolve...")
        os.system("pkill -9 -f 'DaVinci Resolve'")

    except Exception as e:
        print(f"ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        exit_code = 1

        print("Forcefully terminating DaVinci Resolve after error...")
        os.system("pkill -9 -f 'DaVinci Resolve'")

    sys.exit(exit_code)
