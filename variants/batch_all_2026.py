"""
Batch process all 2026 raw files through DaVinci Resolve.
- Copies files locally (DaVinci can't read SURF mount directly)
- Queues batches into DaVinci render queue for efficiency
- Skips files that already exist in post_noncropped
- Restarts DaVinci between batches to prevent memory issues
- Uses signcollect.nl/drs_ep API to coordinate with other machines
"""
import os, sys, glob, shutil, time, json, subprocess, re
from datetime import datetime
sys.path.insert(0, '/Users/signlab/drs')
from python_get_resolve import GetResolve
from pathlib import Path
from drs_render_client import DRSRenderClient

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Paths
BASE_DIR = Path("/Users/gomer/surfnl/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles")
IMPORT_DIR = Path("/Users/gomer/drs-tools/import")
EXPORT_DIR = Path("/Users/gomer/drs-tools/export")
SETTING_PATH = "/Users/gomer/drs-tools/Settings.setting"
LANDSCAPE_SETTING_PATH = "/Users/gomer/drs-tools/landscape.setting"

BATCH_SIZE = 50  # Files per DaVinci session
YEAR = "2026"
MACHINE_NAME = "mac-gomer"

# Initialize render coordination client
render_client = DRSRenderClient(machine_name=MACHINE_NAME)


def clean_rendered_filename(filename):
    match = re.match(r'^([LMR]\d{8}_\d{4})(?:_\d+| \d+)?\.mp4$', filename, re.IGNORECASE)
    if match:
        return f"{match.group(1)}.MP4"
    return filename


def get_video_orientation(filepath):
    if not os.path.isfile(filepath):
        return None, "file_not_found"
    cmd = ["/opt/homebrew/bin/mediainfo", "--Output=JSON", str(filepath)]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
    try:
        data = json.loads(result.stdout)
        video_track = next(t for t in data["media"]["track"] if t["@type"] == "Video")
        width = int(video_track.get("Width", 0))
        height = int(video_track.get("Height", 0))
        rotation = int(float(video_track.get("Rotation", 0)))
        if rotation in [90, 270]:
            width, height = height, width
        orientation = "portrait" if height > width else "landscape"
        return rotation, orientation
    except Exception as e:
        print(f"  Error getting orientation: {e}")
        return 270, "portrait"


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
    subprocess.run(cmd, capture_output=True, text=True)


def minimize_davinci():
    cmd = ["osascript", "-e",
           'tell application "System Events" to set visible of process "DaVinci Resolve" to false']
    subprocess.run(cmd, capture_output=True, text=True)


def get_resolve_with_retry(max_retries=10):
    resolve = GetResolve()
    retry_count = 0
    while not resolve and retry_count < max_retries:
        retry_count += 1
        print(f"  Retry {retry_count}/{max_retries} connecting to DaVinci Resolve...")
        open_davinci_minimized()
        time.sleep(10)
        resolve = GetResolve()
    return resolve


def restart_davinci():
    """Kill and restart DaVinci Resolve for a clean state"""
    print("Restarting DaVinci Resolve...")
    os.system("pkill -9 -f 'DaVinci Resolve'")
    time.sleep(5)
    subprocess.run(["open", "-a", "DaVinci Resolve"], capture_output=True)
    time.sleep(30)
    resolve = get_resolve_with_retry()
    if resolve:
        minimize_davinci()
    return resolve


def collect_files_to_process():
    """Find all unprocessed raw files from 2026"""
    all_files = []

    date_folders = sorted(glob.glob(str(BASE_DIR / f"{YEAR}-*")))
    for folder in date_folders:
        date_str = os.path.basename(folder)
        date_yyyymmdd = date_str.replace("-", "")
        raw_dir = Path(folder) / "raw"
        post_dir = Path(folder) / "post_noncropped"

        if not raw_dir.exists():
            continue

        raw_files = sorted(raw_dir.glob("[LMR]202*.MP4"))
        for raw_file in raw_files:
            filename = raw_file.name
            # Validate date in filename matches folder
            if filename[1:9] != date_yyyymmdd:
                continue
            # Skip if already processed
            if (post_dir / filename).exists():
                continue
            all_files.append((raw_file, post_dir))

    return all_files


def process_batch(batch, resolve):
    """Process a batch of files through DaVinci Resolve.
    Returns (success_count, fail_count, completed_filenames, failed_filenames)"""

    # Clean import and export dirs
    for f in IMPORT_DIR.glob("*"):
        if f.is_file():
            f.unlink()
    for f in EXPORT_DIR.glob("*"):
        if f.is_file():
            f.unlink()

    projectManager = resolve.GetProjectManager()

    # We'll group by orientation to minimize project switches
    portrait_files = []
    landscape_files = []

    completed_filenames = []
    failed_filenames = []

    print(f"  Checking orientations and copying {len(batch)} files locally...")
    for raw_file, post_dir in batch:
        filename = raw_file.name
        rotation, orientation = get_video_orientation(raw_file)

        if orientation == "file_not_found":
            print(f"  SKIP {filename} - file not found")
            failed_filenames.append(filename)
            continue

        # M camera landscape files are skipped (per batch.py logic)
        if orientation == "landscape" and filename.startswith("M"):
            print(f"  SKIP {filename} - M camera landscape")
            completed_filenames.append(filename)  # Mark as done so others don't retry
            continue

        # Copy locally
        local_file = IMPORT_DIR / filename
        try:
            subprocess.run(["rsync", "-a", str(raw_file), str(local_file)],
                           capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            print(f"  SKIP {filename} - copy failed: {e}")
            failed_filenames.append(filename)
            continue

        if orientation == "landscape":
            landscape_files.append((local_file, post_dir, filename))
        else:
            portrait_files.append((local_file, post_dir, filename))

    success_count = 0
    fail_count = len(failed_filenames)

    # Process each orientation group
    for project_name, setting_path, files in [
        ("lala6", SETTING_PATH, portrait_files),
        ("landscape", LANDSCAPE_SETTING_PATH, landscape_files),
    ]:
        if not files:
            continue

        print(f"  Loading project '{project_name}' for {len(files)} files...")
        project = projectManager.LoadProject(project_name)
        if not project:
            print(f"  ERROR: Failed to load project '{project_name}' - skipping {len(files)} files")
            fail_count += len(files)
            failed_filenames.extend([fn for _, _, fn in files])
            continue

        project.DeleteAllRenderJobs()
        mediapool = project.GetMediaPool()

        # Clear media pool
        root_folder = mediapool.GetRootFolder()
        all_clips = get_all_clips(root_folder, [])
        if all_clips:
            mediapool.DeleteClips(all_clips)

        # Add all files to media pool at once
        file_paths = [str(f[0]) for f in files]
        video_items = resolve.GetMediaStorage().AddItemListToMediaPool(file_paths)

        if not video_items:
            # Fallback: try adding one by one via directory
            print(f"  Bulk add failed, trying directory import...")
            video_items = resolve.GetMediaStorage().AddItemListToMediaPool(str(IMPORT_DIR))

        if not video_items:
            print(f"  ERROR: Failed to add files to media pool")
            fail_count += len(files)
            failed_filenames.extend([fn for _, _, fn in files])
            continue

        # Map clips by name
        clip_map = {}
        for item in video_items:
            clip_map[item.GetName()] = item

        # Create timeline + render job for each file
        queued = 0
        queued_filenames = []
        for local_file, post_dir, filename in files:
            base_name = Path(filename).stem
            video_item = clip_map.get(filename)
            if not video_item:
                print(f"  SKIP {filename} - not found in media pool")
                fail_count += 1
                failed_filenames.append(filename)
                continue

            timeline = mediapool.CreateEmptyTimeline(f"TL_{base_name}")
            if not timeline:
                print(f"  SKIP {filename} - timeline creation failed")
                fail_count += 1
                failed_filenames.append(filename)
                continue
            project.SetCurrentTimeline(timeline)

            mediapool.AppendToTimeline([{
                "mediaPoolItem": video_item,
                "trackIndex": 1,
                "startFrame": 0,
            }])

            clips = timeline.GetItemListInTrack("video", 1)
            for item in clips:
                item.ImportFusionComp(setting_path)
                item.LoadFusionCompByName("Composition1")

            project.SetCurrentTimeline(timeline)
            project.SetRenderSettings({
                "TargetDir": str(EXPORT_DIR),
                "Format": "MP4",
                "Codec": "h264",
                "CustomName": base_name,
                "UniqueFilenameStyle": 0,
            })
            job_id = project.AddRenderJob()
            if job_id:
                queued += 1
                queued_filenames.append(filename)
            else:
                print(f"  WARN: Failed to queue {filename}")
                fail_count += 1
                failed_filenames.append(filename)

        if queued == 0:
            print(f"  No files queued for {project_name}")
            continue

        # Start rendering all queued jobs
        print(f"  Rendering {queued} files...")
        minimize_davinci()
        project.StartRendering()

        render_start = time.time()
        while project.IsRenderingInProgress():
            elapsed = int(time.time() - render_start)
            print(f"  Rendering... ({elapsed}s elapsed, project: {project_name})")
            time.sleep(10)

        elapsed = int(time.time() - render_start)
        print(f"  Render complete ({elapsed}s)")

        # Move rendered files to post_noncropped
        rendered_set = set()
        for rendered_file in EXPORT_DIR.glob("*.mp4"):
            clean_name = clean_rendered_filename(rendered_file.name)
            rendered_set.add(clean_name.replace(".MP4", "").replace(".mp4", ""))
            # Find the matching post_dir from our files list
            target_post_dir = None
            for _, post_dir, fn in files:
                if fn.replace(".MP4", "") == clean_name.replace(".MP4", ""):
                    target_post_dir = post_dir
                    break
            # Fallback: derive from filename
            if not target_post_dir:
                date_yyyymmdd = clean_name[1:9]
                date_fmt = f"{date_yyyymmdd[:4]}-{date_yyyymmdd[4:6]}-{date_yyyymmdd[6:8]}"
                target_post_dir = BASE_DIR / date_fmt / "post_noncropped"

            target_post_dir.mkdir(parents=True, exist_ok=True)
            dest = target_post_dir / clean_name

            if dest.exists():
                rendered_file.unlink()
                completed_filenames.append(clean_name)
                success_count += 1
                continue

            try:
                subprocess.run(["rsync", "-a", str(rendered_file), str(dest)],
                               capture_output=True, text=True, check=True)
                rendered_file.unlink()
                success_count += 1
                completed_filenames.append(clean_name)
            except subprocess.CalledProcessError as e:
                print(f"  ERROR moving {clean_name}: {e}")
                fail_count += 1
                failed_filenames.append(clean_name)

        # Check for queued files that didn't produce output (render failed)
        for fn in queued_filenames:
            base = fn.replace(".MP4", "").replace(".mp4", "")
            if base not in rendered_set:
                print(f"  WARN: {fn} was queued but no output found")
                fail_count += 1
                failed_filenames.append(fn)

    # Clean up local files
    for f in IMPORT_DIR.glob("*"):
        if f.is_file():
            f.unlink()
    for f in EXPORT_DIR.glob("*"):
        if f.is_file():
            f.unlink()

    return success_count, fail_count, completed_filenames, failed_filenames


def main():
    print(f"=== Batch All 2026 Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"Machine: {MACHINE_NAME}")

    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    # Clean up stale claims from previous crashed runs
    print("Cleaning up stale claims (>60 min old)...")
    render_client.cleanup_stale(max_age_minutes=60)

    # Collect all candidate files (local check only — no API claims yet)
    print("Scanning for unprocessed files...")
    all_files = collect_files_to_process()
    print(f"Found {len(all_files)} candidate files")

    if not all_files:
        print("Nothing to do!")
        return

    # Split into batches first, then claim per batch
    batches = [all_files[i:i + BATCH_SIZE] for i in range(0, len(all_files), BATCH_SIZE)]
    print(f"Split into {len(batches)} batches of up to {BATCH_SIZE}")

    total_success = 0
    total_fail = 0

    for batch_num, batch in enumerate(batches, 1):
        # Claim only this batch's files via API
        batch_filenames = [raw_file.name for raw_file, _ in batch]
        claimed_filenames = render_client.claim_files(batch_filenames)
        claimed_set = set(claimed_filenames)

        # Filter batch to only files we successfully claimed
        claimed_batch = [(raw_file, post_dir) for raw_file, post_dir in batch
                         if raw_file.name in claimed_set]

        skipped = len(batch) - len(claimed_batch)
        if skipped > 0:
            print(f"  Skipped {skipped} files already claimed by another machine")

        if not claimed_batch:
            print(f"  No files claimed for batch {batch_num} — all taken by other machines, skipping")
            continue

        print(f"\n{'='*60}")
        print(f"BATCH {batch_num}/{len(batches)} ({len(claimed_batch)} files claimed)")
        print(f"Progress: {total_success} done, {total_fail} failed")
        print(f"{'='*60}")

        # Restart DaVinci each batch for clean memory
        resolve = restart_davinci()
        if not resolve:
            print("FATAL: Cannot connect to DaVinci Resolve. Stopping.")
            # Release this batch's claims since we can't process them
            render_client.release_files(list(claimed_set))
            print(f"Released {len(claimed_set)} claims back to pool")
            break

        success, fail, completed_files, failed_files = process_batch(claimed_batch, resolve)
        total_success += success
        total_fail += fail

        # Mark completed files via API
        if completed_files:
            render_client.complete_files(completed_files)
            print(f"  Marked {len(completed_files)} files as completed via API")

        # Release failed files so other machines can retry
        if failed_files:
            render_client.release_files(failed_files)
            print(f"  Released {len(failed_files)} failed files back to pool")

        print(f"Batch {batch_num} complete: {success} ok, {fail} failed")

    # Kill DaVinci at the end
    os.system("pkill -9 -f 'DaVinci Resolve'")

    # Print stats
    stats = render_client.get_stats()
    print(f"\n{'='*60}")
    print(f"=== ALL DONE at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"Total: {total_success} processed, {total_fail} failed")
    print(f"API stats: {stats}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
