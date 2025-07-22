import os, sys, glob, shutil, time, json, subprocess
from datetime import datetime
from python_get_resolve import GetResolve
from pathlib import Path
from video_api_client import VideoAPIClient  # Import the API client

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
        print(f"Error: {e}")
        return 0, "unknown"

def get_resolve_with_retry(max_retries=10):
    """Try to get DaVinci Resolve with retry logic"""
    resolve = GetResolve()
    retry_count = 0
    
    while not resolve and retry_count < max_retries:
        retry_count += 1
        print(f"Retry attempt {retry_count}/{max_retries} to open DaVinci Resolve...")
        os.system("open -a 'DaVinci Resolve'")
        time.sleep(10)
        resolve = GetResolve()
        
    return resolve

def main():
    # Initial resolve connection
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

    # Default project is tyd (the video is squared 3840 due to slight rotation)
    default_project_name = "tyd"
    project = projectManager.GetCurrentProject()
    if not project or project.GetName() != default_project_name:
        print(f"Opening project '{default_project_name}'...")
        project = projectManager.LoadProject(default_project_name)
        if not project:
            print(f"Unable to open project '{default_project_name}'. Exiting.")
            return

    homedir = Path("/Users/signlab/")
    base_dir = Path("/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles")
    import_dir = homedir / "drs/import"
    export_dir = homedir / "drs/export"
    setting_path = homedir / "drs/tyd.setting"
    setting_path = str(setting_path)

    print(import_dir)
    print(export_dir)

    date_threshold = datetime.strptime("2025-03-01", "%Y-%m-%d")
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
        print("No date folders found on or after 2025-04-07.")
        return

    total_processed = 0
    for date_folder in sorted(date_folders):
        print(f"Processing folder: {date_folder}")
        raw_dir = base_dir / date_folder / "raw"
        post_dir = base_dir / date_folder / "tyd_noncropped"
        if not post_dir.exists():
            os.makedirs(post_dir)

        for f in os.listdir(import_dir):
            file_path = import_dir / f
            if file_path.is_file():
                os.remove(file_path)

        raw_files = glob.glob(str(raw_dir / "[M]202*.MP4"))
        if not raw_files:
            print(f"No raw MP4 files found in {raw_dir}.")
            continue

        for raw_file in raw_files:
            raw_file_path = Path(raw_file)
            filename = raw_file_path.name
            #replace .MP4 with .mkv
            filename = filename.replace(".MP4", ".mkv")
            dest_post = post_dir / filename
            if dest_post.exists():
                print(f"File {filename} already exists in post directory; skipping.")
                continue

            #check if the date in file is same as the date in folder
            #date in filename is M20250407
            #date in folder is 2025-04-07
            date_in_filename = filename[1:9]
            date_in_folder = date_folder.replace("-", "")
            if date_in_filename != date_in_folder:
                print(f"Date in filename {date_in_filename} does not match date in folder {date_in_folder}; skipping.")
                continue

            #for now we only want 2025-03-31 and 2025-04-01
            # if date_in_folder not in ["20250331", "20250401"]:
            #     print(f"Date in folder {date_in_folder} is not in the list of dates to process; skipping.")
            #     continue

            # Check video orientation
            rotation, orientation = get_video_orientation(raw_file)
            
            # Determine which project to use based on orientation
            project_name = "tyd"
            
        

            print(f"Processing file: {filename} (orientation: {orientation}, using project: {project_name})")
            #replace .mkv with .MP4
            filename = filename.replace(".mkv", ".MP4")
            temp_import_path = import_dir / filename
            try:
                shutil.copy2(raw_file, temp_import_path)
            except OSError as e:
                print(f"Copy failed for {filename}, retrying... Error: {e}")
                time.sleep(2)
                try:
                    shutil.copy2(raw_file, temp_import_path)
                except Exception as e:
                    print(f"Second attempt failed for {filename}. Skipping. Error: {e}")
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
            video_path_source = import_dir
            #convert video_path_source to a string
            video_path_source = str(video_path_source)
            video_items = resolve.GetMediaStorage().AddItemListToMediaPool(video_path_source)
            if not video_items:
                print("No media found in import directory.")
                return

            # Apply Fusion comp to video clip
            timeline = project.GetCurrentTimeline()
            
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
                comp_path = setting_path
                success = item.ImportFusionComp(comp_path)
                if not success:
                    print(f"Failed to import Fusion comp into clip: {filename}")

            fusion_comp = item.LoadFusionCompByName("Composition1")
                        
            render_settings = {
                "TargetDir": str(export_dir),
                "Format": "mov",   # MOV is QuickTime format
                "Codec": "ProRes",
                "Type": "ProRes 422",
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
                        os.system("open -a 'DaVinci Resolve'")
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
                    if current_time - render_start_time > 1800:  # 30 minutes
                        print("Rendering taking too long, possibly stuck. Aborting.")
                        break
                        
            print(f"Rendering finished for {filename}.")

            rendered_file = export_dir / f"{base_name}.mkv"
            if rendered_file.exists():
                dest_render = post_dir / f"{base_name}.mkv"
                if not dest_render.exists():
                    shutil.move(rendered_file, dest_render)
                else:
                    print(f"Rendered file {base_name}.mp4 already exists in post directory.")
            else:
                print(f"Rendered file {base_name}.mp4 not found in export directory.")

            #let renderServer API know we have rendered the file
            api_client = VideoAPIClient('https://signcollect.nl/renderServer')
            api_client.update_tyd_rendered(filename, 'm_file')


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
                os.system("open -a 'DaVinci Resolve'")
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
            time.sleep(5)  # Short delay after killing DaVinci
            
        except Exception as e:
            print(f"ERROR: Main function crashed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"Error details: {str(e)}")
            
            # Force kill DaVinci Resolve after a crash as well
            print("Forcefully terminating DaVinci Resolve after crash...")
            os.system("pkill -9 -f 'DaVinci Resolve'")
            time.sleep(5)
            
            print("Restarting the main function after a short delay...")
            time.sleep(60)  # Short delay before restarting the function
            continue
            
        print(f"Sleeping for 60 minutes before next run...")
        time.sleep(3600)  # Sleep for 60 minutes (3600 seconds)
