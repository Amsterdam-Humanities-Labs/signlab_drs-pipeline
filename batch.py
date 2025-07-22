import os, sys, glob, shutil, time, json, subprocess
from datetime import datetime
from python_get_resolve import GetResolve
from pathlib import Path
from video_api_client import VideoAPIClient  # Import the API client

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
    
    for attempt in range(retries):
        try:
            # Build rsync command with appropriate flags
            # -a: archive mode (preserves permissions, timestamps, etc.)
            # -v: verbose
            # --progress: show progress during transfer
            cmd = ["rsync", "-av", "--progress", str(source), str(destination)]
            
            # Run rsync command
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            # If successful, return
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
    if not os.path.isfile(filepath):
        print(f"File not found: {filepath}")
        return None, "file_not_found"

    cmd = ["mediainfo", "--Output=JSON", filepath]
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
        print(f"Error getting viceo orientation: {e}")
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



def main():
    homedir = Path("/Users/signlab/")
    base_dir = Path("/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles")
    import_dir = homedir / "drs/import"
    export_dir = homedir / "drs/export"
    setting_path = homedir / "drs/Settings.setting"
    setting_path = str(setting_path)
    landscape_setting_path = homedir / "drs/landscape.setting"
    landscape_setting_path = str(landscape_setting_path)

    print(import_dir)
    print(export_dir)

    date_threshold = datetime.strptime("2024-03-01", "%Y-%m-%d")
    all_items = os.listdir(base_dir)
    date_folders = []
    for item in all_items:
        folder_path = base_dir / item
        if folder_path.is_dir():
            try:
                folder_date = datetime.strptime(item, "%Y-%m-%d")
                if folder_date >= date_threshold:
                    date_folders.append(item)
            except Exception:
                pass

    if not date_folders:
        print("No date folders found on or after 2025-03-01.")
        return

    # Check if there are any raw files to process before opening DaVinci Resolve
    files_to_process = []
    for date_folder in sorted(date_folders):
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
        print("No new raw files found to process.")
        return
        
    if len(files_to_process) < 50:
        print(f"Found only {len(files_to_process)} files to process. Need at least 50 files to start DaVinci Resolve (avoiding corrupted file issues).")
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
    for raw_file, date_folder, post_dir in files_to_process:
        if not post_dir.exists():
            os.makedirs(post_dir)
            
        raw_file_path = Path(raw_file)
        filename = raw_file_path.name
        
        # Check video orientation
        rotation, orientation = get_video_orientation(raw_file)
        
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
            print(f"rsync copy failed for {filename}. Skipping. Error: {e}")
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
                    break
                    
        print(f"Rendering finished for {filename}.")

        api_client = VideoAPIClient('https://signcollect.nl/renderServer')  
        api_client.update_rendered(filename, 'm_file')

        rendered_file = export_dir / f"{base_name}.mp4"
        if rendered_file.exists():
            dest_render = post_dir / f"{base_name}.mp4"
            if not dest_render.exists():
                try:
                    rsync_copy(rendered_file, dest_render)
                    minimize_davinci()  # Minimize DaVinci Resolve after copying file
                    # Delete source file after successful copy to mimic move behavior
                    os.remove(rendered_file)
                    print(f"Successfully moved {base_name}.mp4 to post directory using rsync")
                except OSError as e:
                    print(f"Failed to move {base_name}.mp4 to post directory: {e}")
            else:
                print(f"Rendered file {base_name}.mp4 already exists in post directory.")
        else:
            print(f"Rendered file {base_name}.mp4 not found in export directory.")

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

    print("All files processed.")

if __name__ == "__main__":
    while True:
        try:
            print(f"Starting batch processing at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            main()
            print(f"Batch processing completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Force kill DaVinci Resolve after processing completes
            print("Forcefully terminating DaVinci Resolve to ensure clean state...")
            os.system("pkill -9 -f 'DaVinci Resolve'")
            time.sleep(3600)  # Short delay after killing DaVinci
            
        except Exception as e:
            print(f"ERROR: Main function crashed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"Error details: {str(e)}")
            
            # Force kill DaVinci Resolve after a crash as well
            print("Forcefully terminating DaVinci Resolve after crash...")
            os.system("pkill -9 -f 'DaVinci Resolve'")
            time.sleep(5)
            
            print("Restarting the main function after a short delay...")
            time.sleep(5)  # Short delay before restarting the function
            continue
            
        print(f"Sleeping for 60 minutes before next run...")
        time.sleep(3600)  # Sleep for 60 minutes (3600 seconds)
