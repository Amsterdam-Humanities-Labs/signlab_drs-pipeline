import os
import cv2
import mediapipe as mp
import numpy as np
import time
from datetime import datetime, timedelta
import json
import subprocess
import requests
import tempfile
import shutil
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import sys
import math
from signcollect_monitor import SignCollectMonitor  # Import the monitor client

# Ensure output is flushed immediately to logs
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Initialize the monitor
monitor = SignCollectMonitor(
    client_id='drs-crop-processor',
    client_name='DRS Crop Processor',
    description='MediaPipe pose detection and video cropping pipeline',
    heartbeat_interval=3600
)

# ----- Orientation Detection Function -----

def get_video_orientation(filepath):
    """Check if a video is portrait or landscape using mediainfo"""
    if not os.path.isfile(filepath):
        print(f"File not found: {filepath}")
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
        print(f"Error determining orientation: {e}")
        return 0, "unknown"

# ----- Processing Functions -----f

def extract_frames(video_path, output_dir=None):
    """Extract frames from video and save to disk instead of memory"""
    if output_dir is None:
        # Create a unique temporary directory
        output_dir = os.path.join("/Users/signlab/drs/temp/", f"frames_{uuid.uuid4().hex}")
        os.makedirs(output_dir, exist_ok=True)
    
    cap = cv2.VideoCapture(video_path)
    frame_count = 0
    frame_paths = []
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        frame_path = os.path.join(output_dir, f"frame_{frame_count:06d}.jpg")
        cv2.imwrite(frame_path, frame)
        frame_paths.append(frame_path)
        frame_count += 1
    
    cap.release()
    return frame_paths, output_dir

def get_vertical_bbox(frame_path):
    mp_pose = mp.solutions.pose
    frame = cv2.imread(frame_path)
    with mp_pose.Pose(static_image_mode=True, min_detection_confidence=0.5) as pose:
        img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = pose.process(img_rgb)
        if not results.pose_landmarks:
            return 0, frame.shape[0], frame.shape, None, None, None, None, None, None, None, None
        lm = results.pose_landmarks.landmark
        head_top = min(lm[mp_pose.PoseLandmark.LEFT_EYE].y,
                       lm[mp_pose.PoseLandmark.RIGHT_EYE].y,
                       lm[mp_pose.PoseLandmark.LEFT_EAR].y,
                       lm[mp_pose.PoseLandmark.RIGHT_EAR].y) * frame.shape[0]
        
        #also get head_y coordinate
        head_x = min(lm[mp_pose.PoseLandmark.LEFT_EYE].x,
                       lm[mp_pose.PoseLandmark.RIGHT_EYE].x,
                       lm[mp_pose.PoseLandmark.LEFT_EAR].x,
                       lm[mp_pose.PoseLandmark.RIGHT_EAR].x) * frame.shape[0]
        
        # Calculate hip midpoint coordinates
        left_hip_x = lm[mp_pose.PoseLandmark.LEFT_HIP].x * frame.shape[1]
        left_hip_y = lm[mp_pose.PoseLandmark.LEFT_HIP].y * frame.shape[0]
        right_hip_x = lm[mp_pose.PoseLandmark.RIGHT_HIP].x * frame.shape[1]
        right_hip_y = lm[mp_pose.PoseLandmark.RIGHT_HIP].y * frame.shape[0]

        #calculate left and right shoulder coodinates
        left_shoulder_x = lm[mp_pose.PoseLandmark.LEFT_SHOULDER].x * frame.shape[1]
        left_shoulder_y = lm[mp_pose.PoseLandmark.LEFT_SHOULDER].y * frame.shape[0]
        right_shoulder_x = lm[mp_pose.PoseLandmark.RIGHT_SHOULDER].x * frame.shape[1]
        right_shoulder_y = lm[mp_pose.PoseLandmark.RIGHT_SHOULDER].y * frame.shape[0]

        #calculate position of nose
        nose_x = lm[mp_pose.PoseLandmark.NOSE].x * frame.shape[1]
        nose_y = lm[mp_pose.PoseLandmark.NOSE].y * frame.shape[0]


        
        # Midpoint calculation
        midpoint_x = int((left_hip_x + right_hip_x) / 2)
        waist_y = int((left_hip_y + right_hip_y) / 2)

        print(f"Head top: {head_top}, Waist Y: {waist_y}, Midpoint X: {midpoint_x}")
        head_top = head_top - 300
        print(f"Adjusted head top: {head_top}")
        return int(head_top), waist_y, frame.shape, midpoint_x, left_shoulder_x, right_shoulder_x, left_shoulder_y, right_shoulder_y, head_x, nose_x, nose_y


def save_debug_bbox_frame(frame_path, head, waist, margin, output_path, midpoint_x=None):
    frame = cv2.imread(frame_path)
    new_head = max(0, head - margin)
    debug_frame = frame.copy()
    
    # Draw bounding box
    cv2.rectangle(debug_frame, (0, new_head), (frame.shape[1]-1, waist), (0, 255, 0), 2)
    
    # Draw midpoint if available
    if midpoint_x is not None:
        # Draw vertical line through midpoint
        cv2.line(debug_frame, (midpoint_x, 0), (midpoint_x, frame.shape[0]), (0, 0, 255), 2)
        
        # Draw crosshair at waist midpoint
        radius = 15
        cv2.circle(debug_frame, (midpoint_x, waist), radius, (0, 0, 255), 2)
        cv2.line(debug_frame, (midpoint_x - radius, waist), (midpoint_x + radius, waist), (0, 0, 255), 2)
        cv2.line(debug_frame, (midpoint_x, waist - radius), (midpoint_x, waist + radius), (0, 0, 255), 2)
    
    # Draw overlaid text with information
    cv2.putText(debug_frame, f"Head: {head}, Waist: {waist}, Margin: {margin}", 
                (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    if midpoint_x is not None:
        cv2.putText(debug_frame, f"Midpoint X: {midpoint_x}", 
                    (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    
    # Save both full frame and cropped version
    cv2.imwrite(output_path, debug_frame)
    
    # Create a cropped version to show detail
    if midpoint_x is not None and frame.shape[1] > 1440:
        left_bound = max(0, midpoint_x - 720)
        right_bound = min(frame.shape[1], left_bound + 1440)
        # Adjust left bound if right bound reached edge
        if right_bound == frame.shape[1]:
            left_bound = frame.shape[1] - 1440
        cropped_debug = debug_frame[:, left_bound:right_bound]
        cropped_path = output_path.replace('.jpg', '_cropped.jpg')
        cv2.imwrite(cropped_path, cropped_debug)
    
    print(f"Debug bounding box frame saved as {output_path}")

def process_frames(frame_paths, output_dir=None):
    """
    Process frames saved on disk instead of in memory.
    Now supports both batch processing and single image processing.
    """
    if output_dir is None:
        output_dir = os.path.join(tempfile.gettempdir(), f"processed_{uuid.uuid4().hex}")
        os.makedirs(output_dir, exist_ok=True)
    
    # Process the first frame to get vertical bounds and midpoint
    first_frame_path = frame_paths[0]
    head, waist, frame_shape, midpoint_x, left_shoulder_x, left_shoulder_y, right_shoulder_x, right_shoulder_y, head_x, nose_x, nose_y = get_vertical_bbox(first_frame_path)
    
    # Check if pose was detected
    if midpoint_x is None:  # No pose detected
        raise ValueError("No pose landmarks detected in video - cannot process")
    
    # Create a debug image showing the detected bounding box
    debug_bbox_path = os.path.join(output_dir, "debug_bbox.jpg")
    # save_debug_bbox_frame(first_frame_path, head, waist, 50, debug_bbox_path, midpoint_x)
    
    processed_paths = []

    first_frame = cv2.imread(first_frame_path)
    first_framee = first_frame.copy()

    #calculate angle for when videocamera is tilted
    midpoint_y = first_framee.shape[0]

    # Compute differences
    dx = nose_x - midpoint_x  # ≈ 38.65
    vertical_distance = midpoint_y - nose_y  # ≈ 1021.02

    # Calculate the angle in radians between the vector (pivot -> nose) and the vertical axis
    angle_rad = math.atan2(dx, vertical_distance)

    # Convert angle to degrees
    angle_deg = math.degrees(angle_rad)

    # To correct the tilt, rotate the image by the negative of the calculated angle:
    rotation_angle = angle_deg
    
    for idx, frame_path in enumerate(frame_paths):
        frame = cv2.imread(frame_path)
        processed_image = frame.copy()

        

        rotation_matrix = cv2.getRotationMatrix2D((midpoint_x, midpoint_y), rotation_angle, 1.0)
        processed_image = cv2.warpAffine(processed_image, rotation_matrix, (processed_image.shape[1], processed_image.shape[0]))
        
        if head is not None and waist is not None:
        # Vertically crop the frame from head to waist
            processed_image = processed_image[head:waist, :]
            target_height = waist - head

            # If we have a midpoint, create a horizontal crop centered on it
            if midpoint_x is not None:
                current_width = processed_image.shape[1]

                #target display aspect ratio is 1.15
                target_width = int(target_height * 1.15)
                half_crop = target_width // 2

                # Calculate crop boundaries centered on midpoint
                new_left = max(0, midpoint_x - half_crop)
                new_right = new_left + target_width

                # Adjust if we hit the right edge
                if new_right > current_width:
                    new_right = current_width
                    new_left = max(0, current_width - target_width)

                # Adjust if we hit the left edge
                if new_left == 0:
                    new_right = min(current_width, target_width)

                # Create the final cropped image
                processed_image = processed_image[:, new_left:new_right]

        # Resize to fixed output resolution: 1440 x 1252 (ratio ~1.15)
        OUTPUT_WIDTH = 1440
        OUTPUT_HEIGHT = 1252
        processed_image = cv2.resize(processed_image, (OUTPUT_WIDTH, OUTPUT_HEIGHT), interpolation=cv2.INTER_LANCZOS4)

        # Save the processed frame
        output_frame_path = os.path.join(output_dir, f"processed_{idx:06d}.jpg")
        cv2.imwrite(output_frame_path, processed_image)
        processed_paths.append(output_frame_path)
        
        # For the first frame, save a debug image showing the final crop
        # if idx == 0:
        #     debug_path = os.path.join(output_dir, "debug_final_crop.jpg")
        #     cv2.imwrite(debug_path, processed_image)
    
    return processed_paths, output_dir, head, waist, 50, output_dir

def write_video(frame_paths, output_path, fps):
    """Write video using frames saved on disk"""
    if not frame_paths:
        print("No frames to write to video.")
        return
    
    # Read first frame to get dimensions
    first_frame = cv2.imread(frame_paths[0])
    h, w = first_frame.shape[:2]  # expected dimensions: 1080 x 1440
    
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')  # Using MPEG-4 codec
    out = cv2.VideoWriter(output_path, fourcc, fps, (w, h))
    
    for frame_path in frame_paths:
        frame = cv2.imread(frame_path)
        out.write(frame)
        # Free memory
        del frame
    
    out.release()

def reencode_with_ffmpeg(input_file, output_file=None):
    """Re-encode video using ffmpeg with h264 codec while maintaining original quality"""
    if output_file is None:
        # Create a temporary filename with _h264 suffix
        file_parts = os.path.splitext(input_file)
        output_file = f"{file_parts[0]}_h264{file_parts[1]}"
    
    # Force overwrite: remove output_file if it exists.
    if os.path.exists(output_file):
        os.remove(output_file)
    
    cmd = [
        "/opt/homebrew/bin/ffmpeg", "-y", "-i", input_file, 
        "-c:v", "libx264", "-preset", "medium", 
        "-crf", "18", "-c:a", "copy", 
        output_file
    ]
    
    print(f"Re-encoding video with ffmpeg: {input_file}")
    # subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # print ffmpeg output
    print("FFmpeg output:")
    print(subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode())

    # sys.exit()
    if os.path.exists(output_file):
        print(f"Re-encoding successful: {output_file}")
        return output_file
    else:
        print(f"Re-encoding failed for {input_file}")
        return None

def upload_video(video_path):
    """Upload the video to the processing server"""
    upload_url = "https://signcollect.nl/videoProc/upload_post.php"
    
    if not os.path.exists(video_path):
        print(f"Error: Video file not found at {video_path}")
        return False
    
    try:
        print(f"Uploading video: {video_path}")
        filename = os.path.basename(video_path)
        
        # Create form data with the video file
        files = {'video': (filename, open(video_path, 'rb'), 'video/mp4')}
        
        # Send the POST request with SSL verification disabled
        response = requests.post(upload_url, files=files, verify=False)
        
        if response.status_code == 200:
            print(f"Upload successful: {response.text}")
            return True
        else:
            print(f"Upload failed with status code {response.status_code}: {response.text}")
            return False
    
    except Exception as e:
        print(f"Upload error: {str(e)}")
        return False

def save_error_json(video_path, error_message, error_type="processing_error"):
    """Save error information to a JSON file with the same basename as the video"""
    json_path = os.path.splitext(video_path)[0] + "_error.json"
    error_data = {
        "error": str(error_message),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "video_file": video_path,
        "error_type": error_type,
        "file_size_mb": os.path.getsize(video_path) / (1024 * 1024) if os.path.exists(video_path) else 0
    }
    try:
        with open(json_path, 'w') as f:
            json.dump(error_data, f, indent=2)
        print(f"Error details saved to: {json_path}")
    except Exception as e:
        print(f"Failed to save error JSON: {str(e)}")

def process_video_file(input_file, output_file, temp_dir=None):
    try:
        print(f"Processing file: {input_file}")
        
        # Create a unique temp directory for this task if not provided
        if temp_dir is None:
            temp_dir = os.path.join("/Users/signlab/drs/temp/", f"task_{uuid.uuid4().hex}")
            os.makedirs(temp_dir, exist_ok=True)
        
        # Check video orientation before processing
        rotation, orientation = get_video_orientation(input_file)
        
        # Skip file if larger than 300MB.
        file_size = os.path.getsize(input_file)
        
        # Extract frames to disk
        frames_dir = os.path.join(temp_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        frame_paths, _ = extract_frames(input_file, frames_dir)
        
        if not frame_paths:
            print(f"No frames extracted from {input_file}. Skipping.")
            cleanup_temp_dirs([temp_dir])
            return
        
        cap = cv2.VideoCapture(input_file)
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()

        # Process frames from disk
        processed_dir = os.path.join(temp_dir, "processed")
        os.makedirs(processed_dir, exist_ok=True)
        debug_dir = os.path.join(temp_dir, "debug")
        os.makedirs(debug_dir, exist_ok=True)
        
        processed_paths, _, head, waist, margin, _ = process_frames(frame_paths, processed_dir)
        
        # Write video from processed frames on disk
        write_video(processed_paths, output_file, fps)
        
        # Re-encode with ffmpeg using h264
        reencoded_file = reencode_with_ffmpeg(output_file)
        
        # If re-encoding succeeded, upload the video
        if reencoded_file:
            upload_success = upload_video(reencoded_file)
            if upload_success:
                print(f"Video processing complete for {input_file}")
            else:
                print(f"Failed to upload {reencoded_file}")
        else:
            print(f"Skipping upload due to re-encoding failure: {output_file}")
            
    except ValueError as e:
        if "not enough values to unpack" in str(e):
            error_msg = "MediaPipe pose detection failed - no person detected in video"
            save_error_json(input_file, error_msg, "pose_detection_failed")
        else:
            save_error_json(input_file, str(e), "processing_error")
        print(f"Error processing {input_file}: {str(e)}")
    except Exception as e:
        save_error_json(input_file, str(e), "processing_error")
        print(f"Error processing {input_file}: {str(e)}")
    finally:
        # Always clean up temp directory when done, whether successful or not
        cleanup_temp_dirs([temp_dir])

def cleanup_temp_dirs(dirs):
    """Clean up temporary directories with frame images"""
    for dir_path in dirs:
        if dir_path and os.path.exists(dir_path):
            try:
                shutil.rmtree(dir_path)
                print(f"Cleaned up temporary directory: {dir_path}")
            except Exception as e:
                print(f"Error cleaning up temporary directory {dir_path}: {e}")

def cleanup_old_temp_files(temp_base_dir="/Users/signlab/drs/temp/", max_age_hours=1):
    """Clean up old temporary directories that are older than max_age_hours"""
    if not os.path.exists(temp_base_dir):
        return
    
    current_time = time.time()
    cleaned_count = 0
    
    try:
        for item in os.listdir(temp_base_dir):
            item_path = os.path.join(temp_base_dir, item)
            if os.path.isdir(item_path):
                # Check directory age
                dir_age = current_time - os.path.getmtime(item_path)
                if dir_age > (max_age_hours * 3600):  # Convert hours to seconds
                    try:
                        shutil.rmtree(item_path)
                        cleaned_count += 1
                        print(f"Cleaned up old temporary directory: {item_path}")
                    except Exception as e:
                        print(f"Error cleaning up old directory {item_path}: {e}")
        
        if cleaned_count > 0:
            print(f"Total old directories cleaned: {cleaned_count}")
    except Exception as e:
        print(f"Error during old temp files cleanup: {e}")

def get_directory_size(path):
    """Get total size of a directory in bytes"""
    total_size = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                if os.path.exists(filepath):
                    total_size += os.path.getsize(filepath)
    except Exception as e:
        print(f"Error calculating directory size: {e}")
    return total_size

def cleanup_temp_by_size(temp_base_dir="/Users/signlab/drs/temp/", max_size_gb=10):
    """Clean up temp directory if it exceeds max_size_gb"""
    if not os.path.exists(temp_base_dir):
        return
    
    max_size_bytes = max_size_gb * 1024 * 1024 * 1024
    current_size = get_directory_size(temp_base_dir)
    
    if current_size > max_size_bytes:
        print(f"Temp directory size ({current_size / (1024**3):.2f} GB) exceeds limit ({max_size_gb} GB). Cleaning up...")
        
        # Get all directories with their modification times
        dirs_with_time = []
        try:
            for item in os.listdir(temp_base_dir):
                item_path = os.path.join(temp_base_dir, item)
                if os.path.isdir(item_path):
                    mtime = os.path.getmtime(item_path)
                    dirs_with_time.append((mtime, item_path))
            
            # Sort by modification time (oldest first)
            dirs_with_time.sort()
            
            # Remove oldest directories until size is under limit
            for _, dir_path in dirs_with_time:
                if current_size <= max_size_bytes:
                    break
                try:
                    dir_size = get_directory_size(dir_path)
                    shutil.rmtree(dir_path)
                    current_size -= dir_size
                    print(f"Removed {dir_path} to free up space")
                except Exception as e:
                    print(f"Error removing {dir_path}: {e}")
                    
        except Exception as e:
            print(f"Error during size-based cleanup: {e}")

# ----- Directory Loop -----

def main():
    tasks = []
    homedir = Path("/Users/signlab/")
    base_dir = Path("/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles")    # Loop through each parent directory in base_dir (e.g., "2025-03-01")
    # base_dir = Path("/Users/gomerotterspeer/drs/landscape")    # Loop through each parent directory in base_dir (e.g., "2025-03-01")
    # homedir = Path("/Users/gomerotterspeer/")

    print(f"Processing date directories from the past 2 weeks")

    for subdir in sorted(os.listdir(base_dir), reverse=True):
        date_dir = os.path.join(base_dir, subdir)
        if not os.path.isdir(date_dir):
            continue

        # Parse date from directory name (format: YYYY-MM-DD)
        try:
            dir_date = datetime.strptime(subdir, "%Y-%m-%d")
        except ValueError:
            # Skip directories that don't match date format
            continue

        # Only process folders from the past 31 days
        if dir_date < datetime.now() - timedelta(days=31):
            continue
        
        input_folder = os.path.join(date_dir, "post_noncropped")
        output_folder = os.path.join(date_dir, "post")
        if not os.path.exists(input_folder):
            print(f"Input folder not found: {input_folder}. Skipping {date_dir}.")
            continue
        print(f"Processing folder: {date_dir}")
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
        # Process each video file in the input folder.
        for filename in sorted(os.listdir(input_folder)):
            if not filename.lower().endswith(('.mp4', '.mov', '.avi')):
                continue
            input_file = os.path.join(input_folder, filename)
            output_file = os.path.join(output_folder, filename)
            
            # Check for existing error JSON - ignore and reprocess
            # error_json_path = os.path.splitext(input_file)[0] + "_error.json"
            # if os.path.exists(error_json_path):
            #     print(f"Skipping {filename} - error file exists: {error_json_path}")
            #     continue
            
            # Check for the _h264.mp4 version since that's the actual final output
            file_parts = os.path.splitext(filename)
            h264_filename = f"{file_parts[0]}_h264{file_parts[1]}"
            h264_output_file = os.path.join(output_folder, h264_filename)
            if os.path.exists(h264_output_file):
                print(f"Output file already exists for {h264_filename}. Skipping.")
                continue
            # if os.path.getsize(input_file) > 300 * 1024 * 1024:
            #     print(f"Skipping {filename} because it is larger than 300MB.")
            #     continue

            #check if the date in file is same as the date in folder
            #date in filename is M20250407
            #date in folder is 2025-04-07
            date_in_filename = filename[1:9]
            # print(f"Date in filename: {date_in_filename}")
            #convert date_dir to YYYYMMDD format by getting the last 10 characters
            #date_dir is 2025-04-07
            date_in_folder = date_dir[-10:]
            date_in_folder = date_in_folder.replace("-", "")
            if date_in_filename != date_in_folder:
                print(f"Date in filename {date_in_filename} does not match date in folder {date_in_folder}; skipping.")
                continue

            first_char = filename[0].upper()
            if first_char == 'L':
                shift_direction = 'L'
            elif first_char == 'R':
                shift_direction = 'R'
            else:
                shift_direction = 'M'

            tasks.append((input_file, output_file, shift_direction))
    
    if tasks:
        with ProcessPoolExecutor(max_workers=5) as executor:
            futures_to_temp_dirs = {}
            
            # Create a unique temp directory for each task and submit
            for task in tasks:
                input_file, output_file, shift_direction = task
                task_temp_dir = os.path.join("/Users/signlab/drs/temp/", f"task_{uuid.uuid4().hex}")
                os.makedirs(task_temp_dir, exist_ok=True)
                
                # Submit task with temp directory
                future = executor.submit(process_video_file, input_file, output_file, task_temp_dir)
                futures_to_temp_dirs[future] = task_temp_dir
            
            # Process completed futures
            for future in as_completed(futures_to_temp_dirs.keys()):
                try:
                    future.result()
                except Exception as e:
                    print(f"Task execution failed: {e}")
                finally:
                    # Extra cleanup in case the process_video_file didn't clean up
                    temp_dir = futures_to_temp_dirs[future]
                    if os.path.exists(temp_dir):
                        cleanup_temp_dirs([temp_dir])
    else:
        print("No tasks to process.")

if __name__ == "__main__":
    # Register with monitoring system
    monitor.register()

    # First clean up the temp directory
    temp_dir = "/Users/signlab/drs/temp/"
    if os.path.exists(temp_dir):
        print("Cleaning up temp directory before starting...")
        cleanup_old_temp_files(temp_dir, max_age_hours=0)  # Clean all old files
        cleanup_temp_by_size(temp_dir, max_size_gb=10)  # Ensure size is under limit
    else:
        os.makedirs(temp_dir, exist_ok=True)

    # Run main() in an infinite loop every hour if it's not currently running.
    while True:
        try:
            # Send heartbeat at start of each cycle
            monitor.send_heartbeat()

            # Clean up old temp files before each run
            cleanup_old_temp_files(temp_dir, max_age_hours=1)
            cleanup_temp_by_size(temp_dir, max_size_gb=10)

            main()
        except Exception as e:
            print(f"Error in main execution: {e}")

        # Clean up after processing
        cleanup_old_temp_files(temp_dir, max_age_hours=1)

        print("Sleeping for one hour before next run...")
        time.sleep(900)
