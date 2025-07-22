import os
import cv2
import mediapipe as mp
import numpy as np
import time
import json
import subprocess
import requests
import tempfile
import shutil
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# ----- Orientation Detection Function -----

def get_video_orientation(filepath):
    """Check if a video is portrait or landscape using mediainfo"""
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
        print(f"Error determining orientation: {e}")
        return 0, "unknown"

# ----- Processing Functions -----

def extract_frames(video_path, output_dir=None):
    """Extract frames from video and save to disk instead of memory"""
    if output_dir is None:
        # Create a unique temporary directory
        output_dir = os.path.join(tempfile.gettempdir(), f"frames_{uuid.uuid4().hex}")
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

def process_frames_landscape(frame_paths, output_dir=None):
    """
    Process landscape frames:
    - Maintain aspect ratio
    - Center crop to 1440x1080 resolution (4:3 aspect ratio)
    """
    if output_dir is None:
        # Create a unique temporary directory for processed frames
        output_dir = os.path.join(tempfile.gettempdir(), f"processed_{uuid.uuid4().hex}")
        os.makedirs(output_dir, exist_ok=True)
    
    processed_paths = []
    
    # Target dimensions for landscape video matching portrait output width
    target_height = 1080
    target_width = 1440   # Changed from 1920 to match portrait output
    
    for i, frame_path in enumerate(frame_paths):
        frame = cv2.imread(frame_path)
        h, w = frame.shape[:2]
        
        # Calculate resize ratio to maintain aspect ratio
        ratio = max(target_height / h, target_width / w)
        new_h, new_w = int(h * ratio), int(w * ratio)
        
        # Resize the frame
        resized = cv2.resize(frame, (new_w, new_h))
        
        # Center crop
        y_offset = (new_h - target_height) // 2 if new_h > target_height else 0
        x_offset = (new_w - target_width) // 2 if new_w > target_width else 0
        
        # Create target frame
        if new_h >= target_height and new_w >= target_width:
            # If frame is large enough, crop it
            final_frame = resized[y_offset:y_offset+target_height, 
                                   x_offset:x_offset+target_width]
        else:
            # If frame is too small, create black canvas and place resized frame
            final_frame = np.zeros((target_height, target_width, 3), dtype=np.uint8)
            paste_y = (target_height - new_h) // 2 if new_h < target_height else 0
            paste_x = (target_width - new_w) // 2 if new_w < target_width else 0
            final_frame[paste_y:paste_y+new_h, paste_x:paste_x+new_w] = resized
        
        # Save processed frame to disk
        processed_path = os.path.join(output_dir, f"processed_{i:06d}.jpg")
        cv2.imwrite(processed_path, final_frame)
        processed_paths.append(processed_path)
        
        # Free memory
        del frame, resized, final_frame
    
    return processed_paths, output_dir

def write_video(frame_paths, output_path, fps):
    """Write video using frames saved on disk"""
    if not frame_paths:
        print("No frames to write to video.")
        return
    
    # Read first frame to get dimensions
    first_frame = cv2.imread(frame_paths[0])
    h, w = first_frame.shape[:2]  # expected dimensions: 1080 x 1920
    
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
    
    cmd = [
        "ffmpeg", "-y", "-i", input_file, 
        "-c:v", "libx264", "-preset", "medium", 
        "-crf", "18", "-c:a", "copy", 
        output_file
    ]
    
    print(f"Re-encoding video with ffmpeg: {input_file}")
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    if os.path.exists(output_file):
        print(f"Re-encoding successful: {output_file}")
        return output_file
    else:
        print(f"Re-encoding failed for {input_file}")
        return None

def upload_video(video_path):
    """Upload the video to the processing server"""
    upload_url = "https://leffe.science.uva.nl:8043/videoProc/upload_post.php"
    
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

def process_video_file(input_file, output_file, shift_direction):
    print(f"Processing landscape file: {input_file}")
    
    # Extract filename for checking if it starts with 'M'
    filename = os.path.basename(input_file)
    if not filename.upper().startswith('M'):
        print(f"Skipping {input_file}: Filename does not start with 'M'")
        return
    
    # Check video orientation before processing
    rotation, orientation = get_video_orientation(input_file)
    if orientation != "landscape":
        print(f"Skipping {input_file}: {orientation} orientation detected (expected landscape)")
        return
    
    # Extract frames to disk
    frame_paths, frames_dir = extract_frames(input_file)
    if not frame_paths:
        print(f"No frames extracted from {input_file}. Skipping.")
        cleanup_temp_dirs([frames_dir])
        return
    
    cap = cv2.VideoCapture(input_file)
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()

    # Process frames from disk - using landscape processing
    processed_paths, processed_dir = process_frames_landscape(frame_paths)
    
    # Write video from processed frames on disk
    write_video(processed_paths, output_file, fps)
    print(f"Finished processing landscape video {input_file} and saved to {output_file}")
    
    # Clean up temporary directories with frame images
    cleanup_temp_dirs([frames_dir, processed_dir])
    
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

def cleanup_temp_dirs(dirs):
    """Clean up temporary directories with frame images"""
    for dir_path in dirs:
        if dir_path and os.path.exists(dir_path):
            try:
                shutil.rmtree(dir_path)
                print(f"Cleaned up temporary directory: {dir_path}")
            except Exception as e:
                print(f"Error cleaning up temporary directory {dir_path}: {e}")

# ----- Directory Loop -----

def main():
    tasks = []
    homedir = Path("/Users/signlab/")
    base_dir = Path("/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles")    # Loop through each parent directory in base_dir (e.g., "2025-03-01")
    for subdir in sorted(os.listdir(base_dir)):
        date_dir = os.path.join(base_dir, subdir)
        if not os.path.isdir(date_dir):
            continue
        #if 2025 is not in date_dir then continue
        if "2025-03-25" not in date_dir:
            continue
        
        input_folder = os.path.join(date_dir, "post_noncropped")
        output_folder = os.path.join(date_dir, "post")  # Different output folder for landscape videos
        if not os.path.exists(input_folder):
            print(f"Input folder not found: {input_folder}. Skipping {date_dir}.")
            continue
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
        
        # Process each video file in the input folder.
        for filename in sorted(os.listdir(input_folder)):
            if not filename.lower().endswith(('.mp4', '.mov', '.avi')):
                continue
            if not filename.upper().startswith('M'):
                continue  # Skip files not starting with M
                
            input_file = os.path.join(input_folder, filename)
            output_file = os.path.join(output_folder, filename)
            
            if os.path.exists(output_file):
                print(f"Output file already exists for {filename}. Skipping.")
                continue
                
            # Check orientation first to filter out non-landscape videos
            rotation, orientation = get_video_orientation(input_file)
            if orientation != "landscape":
                print(f"Skipping {filename}: Not a landscape video")
                continue
                
            # For landscape videos, shift_direction is not used but we'll pass 'M' anyway
            tasks.append((input_file, output_file, 'M'))
    
    if tasks:
        with ProcessPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(process_video_file, t[0], t[1], t[2]) for t in tasks]
            for future in as_completed(futures):
                future.result()

    else:
        print("No landscape videos to process.")

if __name__ == "__main__":
    # Run main() in an infinite loop every hour if it's not currently running.
    while True:
        main()
        print("Sleeping for one hour before next run...")
        time.sleep(3600)
