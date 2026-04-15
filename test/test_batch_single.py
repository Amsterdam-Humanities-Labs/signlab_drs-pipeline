"""
Quick test script: process a single file through DaVinci Resolve using batch_queue logic.
Adapted paths for /Users/gomer machine.
"""
import os, sys, time, json, subprocess
from datetime import datetime
from python_get_resolve import GetResolve
from pathlib import Path

# Ensure output is flushed immediately
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Paths adapted for this machine
BASE_DIR = Path("/Users/gomer/surfnl/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles")
export_dir = Path("/Users/gomer/drs-tools/export")
setting_path = "/Users/gomer/drs-tools/Settings.setting"

# The file to test with
RAW_FILE = BASE_DIR / "2026-02-16" / "raw" / "M20260216_4332.MP4"
POST_DIR = BASE_DIR / "2026-02-16" / "post_noncropped"

def get_all_clips(folder, clips_list):
    clips_list.extend(folder.GetClipList())
    for subfolder in folder.GetSubFolderList():
        get_all_clips(subfolder, clips_list)
    return clips_list

def open_davinci_minimized():
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
    cmd = [
        "osascript",
        "-e", 'tell application "System Events" to set visible of process "DaVinci Resolve" to false'
    ]
    subprocess.run(cmd, capture_output=True, text=True)

def get_resolve_with_retry(max_retries=10):
    resolve = GetResolve()
    retry_count = 0
    while not resolve and retry_count < max_retries:
        retry_count += 1
        print(f"Retry attempt {retry_count}/{max_retries} to connect to DaVinci Resolve...")
        open_davinci_minimized()
        time.sleep(10)
        resolve = GetResolve()
    return resolve

def main():
    print(f"=== Single File Test Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"Input:  {RAW_FILE}")
    print(f"Output: {POST_DIR}")

    # Verify input file exists
    if not RAW_FILE.exists():
        print(f"ERROR: Input file not found: {RAW_FILE}")
        return False

    # Ensure output dirs exist
    POST_DIR.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)

    # Check if already processed
    output_file = POST_DIR / RAW_FILE.name
    if output_file.exists():
        print(f"Already processed: {output_file}")
        return True

    # Clean export directory
    for f in export_dir.glob("*"):
        if f.is_file():
            f.unlink()

    # Connect to DaVinci Resolve
    print("Connecting to DaVinci Resolve...")
    resolve = get_resolve_with_retry()
    if not resolve:
        print("ERROR: Failed to connect to DaVinci Resolve")
        return False

    print("Connected to DaVinci Resolve")
    minimize_davinci()

    projectManager = resolve.GetProjectManager()

    # Load the lala6 project (portrait processing)
    print("Loading project 'lala6'...")
    project = projectManager.LoadProject("lala6")
    if not project:
        print("ERROR: Failed to load project 'lala6'. Make sure it exists in DaVinci Resolve.")
        print("Available projects:")
        # Try to list projects
        try:
            proj_list = projectManager.GetProjectListInCurrentFolder()
            for p in proj_list:
                print(f"  - {p}")
        except:
            print("  (could not list projects)")
        return False

    print(f"Project loaded: {project.GetName()}")

    # Clear existing render jobs
    project.DeleteAllRenderJobs()

    # Clear media pool
    mediapool = project.GetMediaPool()
    root_folder = mediapool.GetRootFolder()
    all_clips = get_all_clips(root_folder, [])
    if all_clips:
        mediapool.DeleteClips(all_clips)
        print(f"Cleared {len(all_clips)} clips from media pool")

    # Copy file locally first (DaVinci can't read from network mounts directly)
    import_dir = Path("/Users/gomer/drs-tools/import")
    import_dir.mkdir(parents=True, exist_ok=True)
    filename = RAW_FILE.name
    base_name = RAW_FILE.stem
    local_file = import_dir / filename

    print(f"Copying {filename} to local import dir...")
    subprocess.run(["rsync", "-av", "--progress", str(RAW_FILE), str(local_file)],
                   capture_output=True, text=True, check=True)
    print(f"Copy complete ({local_file.stat().st_size / 1024 / 1024:.1f} MB)")

    # Add local file to media pool
    print(f"Adding {filename} to media pool...")
    video_items = resolve.GetMediaStorage().AddItemListToMediaPool(str(import_dir))

    if not video_items:
        print(f"ERROR: Failed to add {filename} to media pool")
        return False

    print(f"Added {len(video_items)} item(s) to media pool")

    # Create timeline
    timeline_name = f"Timeline_{base_name}"
    timeline = mediapool.CreateEmptyTimeline(timeline_name)
    if not timeline:
        print("ERROR: Failed to create timeline")
        return False
    project.SetCurrentTimeline(timeline)

    # Add video to timeline
    clip_info = {
        "mediaPoolItem": video_items[0],
        "trackIndex": 1,
        "startFrame": 0,
    }
    mediapool.AppendToTimeline([clip_info])
    print("Video added to timeline")

    # Apply Fusion composition
    clips = timeline.GetItemListInTrack("video", 1)
    for item in clips:
        success = item.ImportFusionComp(setting_path)
        if success:
            print("Fusion composition applied successfully")
            item.LoadFusionCompByName("Composition1")
        else:
            print("WARNING: Failed to import Fusion comp")

    # Set render settings
    project.SetCurrentTimeline(timeline)
    render_settings = {
        "TargetDir": str(export_dir),
        "Format": "MP4",
        "Codec": "h264",
        "CustomName": base_name,
        "UniqueFilenameStyle": 0,
    }
    project.SetRenderSettings(render_settings)

    # Add render job and start
    job_id = project.AddRenderJob()
    if not job_id:
        print("ERROR: Failed to add render job")
        return False

    print(f"Render job added (ID: {job_id}). Starting render...")
    project.StartRendering()

    # Monitor rendering
    render_start = time.time()
    while project.IsRenderingInProgress():
        elapsed = int(time.time() - render_start)
        print(f"Rendering... ({elapsed}s elapsed)")
        time.sleep(5)

    elapsed = int(time.time() - render_start)
    print(f"Render complete! ({elapsed}s)")

    # Move rendered file to post_noncropped
    rendered_files = list(export_dir.glob("*.mp4"))
    if not rendered_files:
        print("ERROR: No rendered file found in export directory")
        return False

    for rendered_file in rendered_files:
        dest = POST_DIR / f"{base_name}.MP4"
        print(f"Copying {rendered_file.name} -> {dest}")
        subprocess.run(["rsync", "-av", "--progress", str(rendered_file), str(dest)],
                       capture_output=True, text=True, check=True)
        rendered_file.unlink()
        print(f"Done! Output: {dest}")

    # Clean up local import copy
    if local_file.exists():
        local_file.unlink()
        print("Cleaned up local import file")

    print(f"\n=== Test Completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
