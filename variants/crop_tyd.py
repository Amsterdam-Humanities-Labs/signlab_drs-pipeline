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
import sys
import math
from ultralytics import YOLO  # Add import for YOLO
sys.path.insert(0, '/Users/signlab/drs/services')
sys.path.insert(0, '/Users/signlab/drs/shared')
from video_api_client import VideoAPIClient  # Import the API client

# Global variables to store pose detection results from first frame
GLOBAL_HEAD = None
GLOBAL_WAIST = None
GLOBAL_MIDPOINT_X = None
GLOBAL_NOSE_X = None
GLOBAL_NOSE_Y = None
GLOBAL_ROTATION_ANGLE = None
GLOBAL_IMG_HEIGHT = None
GLOBAL_IMG_WIDTH = None
GLOBAL_HEAD_NEW = None
GLOBAL_WAIST_NEW = None
GLOBAL_NOSE_NEW = None
GLOBAL_NEW_IMAGE_HEIGHT = None
GLOBAL_NEW_IMAGE_WIDTH = None
GLOBAL_IDEAL_WIDTH = None
GLOBAL_TOP_NEW = None
GLOBAL_WAIST_NEW = None
GLOBAL_NEW_LEFT_NEW = None
GLOBAL_NEW_RIGHT_NEW = None
tasks_temp = 0
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

# ----- Processing Functions -----f

def extract_frames(video_path, output_dir=None):
    """Extract frames from video and save to disk with highest possible quality"""
    if output_dir is None:
        output_dir = os.path.join("/Users/signlab/drs/temp/", f"frames_{uuid.uuid4().hex}")
        os.makedirs(output_dir, exist_ok=True)
    
    cap = cv2.VideoCapture(video_path)
    frame_count = 0
    frame_paths = []
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        # Save with highest quality (100)
        frame_path = os.path.join(output_dir, f"frame_{frame_count:06d}.png")
        cv2.imwrite(frame_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 100])
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
            return 0, frame.shape[0], frame.shape, None
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
    if midpoint_x is not None and frame.shape[1] > 1920:
        left_bound = max(0, midpoint_x - 960)
        right_bound = min(frame.shape[1], left_bound + 1920)
        # Adjust left bound if right bound reached edge
        if right_bound == frame.shape[1]:
            left_bound = frame.shape[1] - 1920
        cropped_debug = debug_frame[:, left_bound:right_bound]
        cropped_path = output_path.replace('.jpg', '_cropped.jpg')
        cv2.imwrite(cropped_path, cropped_debug)
    
    print(f"Debug bounding box frame saved as {output_path}")

def process_frames(frame_paths, output_dir=None):
    """
    Process frames saved on disk instead of in memory.
    Applies rotation, cropping, resizing, and canvas placement based on the first frame's analysis.
    """
    processed_paths = []
    if not frame_paths:
        print("No frame paths provided to process_frames.")
        return [], None, 0, 0, 0, None # Return empty list and default values

    if output_dir is None:
        output_dir = os.path.join(tempfile.gettempdir(), f"processed_{uuid.uuid4().hex}")
        os.makedirs(output_dir, exist_ok=True)

    # 1. Analyze the first frame to determine parameters
    first_frame_path = frame_paths[0]
    head, waist, frame_shape, midpoint_x, _, _, _, _, _, nose_x, nose_y = get_vertical_bbox(first_frame_path)

    if frame_shape is None:
        print(f"Could not read frame shape from {first_frame_path}. Skipping processing.")
        # Attempt to read shape directly if get_vertical_bbox failed partially
        try:
            first_frame_img = cv2.imread(first_frame_path)
            if first_frame_img is None:
                 raise ValueError("First frame image is None")
            frame_shape = first_frame_img.shape
            img_height, img_width = frame_shape[:2]
            # Provide default values if pose detection failed completely
            head = 0
            waist = img_height
            midpoint_x = img_width // 2
            nose_x = midpoint_x
            nose_y = img_height // 3
            rotation_angle = 0
            print("Warning: Pose detection failed on the first frame. Using default values.")
        except Exception as e:
             print(f"Error reading first frame {first_frame_path}: {e}. Cannot proceed.")
             return [], None, 0, 0, 0, None


    img_height, img_width = frame_shape[:2]

    # Handle cases where pose detection might have partially failed
    if head is None or waist is None or midpoint_x is None or nose_x is None or nose_y is None:
        print("Warning: Pose detection partially failed on the first frame. Using defaults for missing values.")
        head = head if head is not None else 0
        waist = waist if waist is not None else img_height
        midpoint_x = midpoint_x if midpoint_x is not None else img_width // 2
        nose_x = nose_x if nose_x is not None else midpoint_x
        nose_y = nose_y if nose_y is not None else img_height // 3
        rotation_angle = 0
    else:
        # 2. Calculate Rotation Angle based on the first frame
        pivot_y = img_height # Use image bottom center as the pivot point
        dx = nose_x - midpoint_x
        vertical_distance = pivot_y - nose_y

        if abs(vertical_distance) < 1e-6: # Avoid division by zero
            angle_rad = math.pi / 2 if dx > 0 else (-math.pi / 2 if dx < 0 else 0)
        else:
            angle_rad = math.atan2(dx, vertical_distance)

        angle_deg = math.degrees(angle_rad)
        rotation_angle = angle_deg # Rotate by this angle to make the torso vertical

    # 3. Calculate Rotation Matrix based on the first frame's analysis
    # Use the calculated midpoint_x and the bottom of the image as rotation center
    rot_center_x = midpoint_x
    rot_center_y = img_height # Rotate around the estimated ground point below the midpoint
    rotation_matrix = cv2.getRotationMatrix2D((rot_center_x, rot_center_y), rotation_angle, 1.0)

    # --- Calculate initial crop parameters based on rotated first frame coordinates ---
    # Create points for head-top-center and waist-center before rotation
    head_pt = np.array([midpoint_x, head, 1])
    waist_pt = np.array([midpoint_x, waist, 1])
    # Apply rotation matrix to these points to find their positions *after* rotation
    rotated_head_pt = rotation_matrix @ head_pt
    rotated_waist_pt = rotation_matrix @ waist_pt
    # Update head, waist y-coordinates and midpoint_x based on rotation
    rotated_head_y = int(rotated_head_pt[1])
    rotated_waist_y = int(rotated_waist_pt[1])
    rotated_midpoint_x = int(rotated_head_pt[0]) # Use the rotated horizontal position

    # Ensure coordinates are within bounds after theoretical rotation
    rotated_head_y = max(0, min(img_height, rotated_head_y))
    rotated_waist_y = max(0, min(img_height, rotated_waist_y))
    rotated_midpoint_x = max(0, min(img_width, rotated_midpoint_x))

    target_height = rotated_waist_y - rotated_head_y

    

    # Check for invalid vertical crop dimensions
    if rotated_head_y >= rotated_waist_y:
        print(f"Warning: Invalid vertical crop dimensions calculated (head={rotated_head_y}, waist={rotated_waist_y}). Using full height.")
        rotated_head_y = 0
        rotated_waist_y = img_height

    # 4. Process all frames using the calculated parameters
    for idx, frame_path in enumerate(frame_paths):
        frame = cv2.imread(frame_path)
        if frame is None:
            print(f"Warning: Could not read frame {frame_path}. Skipping.")
            continue

        # Make a copy to avoid modifying the original frame object if reused
        processed_image = frame.copy()

        # 5. Rotate Image using the pre-calculated matrix
        processed_image = cv2.warpAffine(processed_image, rotation_matrix, (img_width, img_height), borderMode=cv2.BORDER_CONSTANT, borderValue=(0,0,0)) # Fill with black

        # 6. Crop Image using the pre-calculated coordinates
        # Vertical crop first
        processed_image = processed_image[rotated_head_y:rotated_waist_y, :]

        if processed_image.shape[0] <= 0 or processed_image.shape[1] <= 0:
             print(f"Warning: Invalid crop resulted in zero dimensions for frame {idx}. Skipping frame.")
             continue

        #center the image
    # If we have a midpoint, create a horizontal crop centered on it
        if midpoint_x is not None:
            crop_width = 3840
            half_crop = crop_width // 2
            current_width = processed_image.shape[1]
            
            # Calculate crop boundaries
            new_left = max(0, midpoint_x - half_crop)
            new_right = min(current_width, new_left + crop_width)
            
            # Adjust if we hit the right edge
            if new_right == current_width:
                new_left = max(0, current_width - crop_width)
            
            #target display aspect ratio is 16:9 so 1,77
            target_width = int(target_height * 1.77)

            if new_right - new_left < target_width:
                # Calculate new left and right based on target width
                new_left = max(0, midpoint_x - target_width // 2)
                new_right = min(current_width, new_left + target_width)
            
            # print(f"New left: {new_left}, New right: {new_right}, Current width: {current_width}")
            #if new_left is maxed out, then adjust new_right accordingly
            if new_left == 0:
                #calculate new_left with midpoint_x and adjust new difference with new_right
                new_right = midpoint_x + (midpoint_x - new_left) - 50
            if new_right == current_width:
                #calculate new_right with midpoint_x and adjust new difference with new_left
                new_left = midpoint_x - (new_right - midpoint_x) + 50

        processed_image = processed_image[:, new_left:new_right]


        h, w = processed_image.shape[:2]

        # 1. Optional: apply an initial scaling of 1.1
        initial_scale = 1.1
        init_w = int(w * initial_scale)
        init_h = int(h * initial_scale)
        resized_image = cv2.resize(processed_image, (init_w, init_h), interpolation=cv2.INTER_AREA)

        # Target canvas dimensions
        canvas_w, canvas_h = 1920, 1080

        # 2. Compute scaling factors to cover the canvas.
        #    We want the image to fill the canvas completely,
        #    so we choose the scale factor that makes one dimension at least as large as the canvas.
        scale_w = canvas_w / init_w
        scale_h = canvas_h / init_h

        # Use the larger scaling factor to ensure the image covers the canvas.
        final_scale = max(scale_w, scale_h)
        final_w = int(init_w * final_scale)
        final_h = int(init_h * final_scale)

        # 3. Resize image to the new dimensions
        final_resized = cv2.resize(resized_image, (final_w, final_h), interpolation=cv2.INTER_AREA)

        # 4. Crop the center to the canvas dimensions (1920x1080)
        # Calculate offsets for centered crop.
        x_offset = (final_w - canvas_w) // 2
        y_offset = (final_h - canvas_h) // 2

        # Perform the crop.
        final_cropped = final_resized[y_offset:y_offset + canvas_h, x_offset:x_offset + canvas_w]


        #12. Draw a triangle on the top left corner, fill the triangle with white color and no borders
        triangle_points = np.array([[0, 0], [200, 0], [0, 300]], np.int32)
        cv2.fillPoly(final_cropped, [triangle_points], (255, 255, 255))


        #13. paste png image on the left right corner of canvas
        logo = cv2.imread("/Users/signlab/drs/tyd_logo.png", cv2.IMREAD_UNCHANGED)
        #paste the logo on canvas on the top left corner
        if logo is not None and logo.shape[2] == 4:
            b, g, r, a = cv2.split(logo)
            
            # Create a white background for the logo (all values 255)
            white_bg = np.ones_like(a, dtype=logo.dtype) * 255
            
            # Normalize the alpha channel to be in the range 0.0-1.0
            alpha = a.astype(float) / 255.0
            
            # Blend each color channel with the white background:
            # blended = logo_color * alpha + white * (1 - alpha)
            b = b.astype(float) * alpha + white_bg.astype(float) * (1 - alpha)
            g = g.astype(float) * alpha + white_bg.astype(float) * (1 - alpha)
            r = r.astype(float) * alpha + white_bg.astype(float) * (1 - alpha)
            
            # Merge the blended channels and convert back to uint8
            logo = cv2.merge([b.astype(np.uint8), g.astype(np.uint8), r.astype(np.uint8)])
            logo_h, logo_w = logo.shape[:2]
            # Resize logo to fit within 200x200
            logo_scale = min(100 / logo_w, 100 / logo_h)
            new_logo_w = int(logo_w * logo_scale)
            new_logo_h = int(logo_h * logo_scale)
            resized_logo = cv2.resize(logo, (new_logo_w, new_logo_h), interpolation=cv2.INTER_AREA)

            # Calculate paste position for the logo on the left corner
            paste_x = 25
            paste_y = 25

            # Paste the resized logo onto the canvas
            final_cropped[paste_y:paste_y + new_logo_h, paste_x:paste_x + new_logo_w] = resized_logo

        else:
            print(f"Warning: Logo image not found or invalid at /Users/signlab/drs/temp/logo.png. Skipping logo paste.")

        # 12. Save the processed frame (canvas)
        output_frame_path = os.path.join(output_dir, f"processed_{idx:06d}.jpg")
        cv2.imwrite(output_frame_path, final_cropped) # Save the canvas
        processed_paths.append(output_frame_path)

        # Optional: Save debug image for the first frame showing the final result
        # if idx == 0:
        #     debug_path = os.path.join(output_dir, "debug_final_output.jpg")
        #     cv2.imwrite(debug_path, canvas)

    print(f"{len(processed_paths)} frames processed and saved to {output_dir}")

    # Return the paths to the processed frames (on canvas) and other info
    # Using the initially calculated head/waist for reference, though rotated ones were used for cropping
    return processed_paths, output_dir, head, waist, 50, output_dir # 50 is a placeholder margin


def write_video(frame_paths, output_path, fps):
    """Write video using frames saved on disk with high quality (60 Mbps)"""
    if not frame_paths:
        print("No frames to write to video.")
        return
    
    # Read first frame to get dimensions
    first_frame = cv2.imread(frame_paths[0])
    h, w = first_frame.shape[:2]  # expected dimensions: 1920 * 1080
    
    # Use high quality codec settings
    fourcc = cv2.VideoWriter_fourcc(*'FFV1')  # FFV1 is a lossless codec
    
    # Calculate bitrate: 60 Mbps = 60,000,000 bits per second
    # OpenCV doesn't have direct bitrate control, but we can get close with proper settings
    out = cv2.VideoWriter(output_path, fourcc, fps, (w, h), isColor=True)
    
    # In OpenCV 4.5.x+ you can set parameters like this (uncomment if your OpenCV version supports it):
    out.set(cv2.VIDEOWRITER_PROP_QUALITY, 100)  # 100 is highest quality
    
    print(f"Writing video with high quality to {output_path}")
    
    for frame_path in frame_paths:
        frame = cv2.imread(frame_path)
        if frame is not None:
            out.write(frame)
            # Free memory
            del frame
        else:
            print(f"Warning: Could not read frame {frame_path}")
    
    out.release()
    return os.path.exists(output_path)

def reencode_with_ffmpeg(input_file, output_file=None):
    """Re-encode video using ffmpeg with h264 codec while maintaining original quality"""
    if output_file is None:
        # Create a temporary filename with _h264 suffix
        file_parts = os.path.splitext(input_file)
        output_file = f"{file_parts[0]}_h264{file_parts[1]}"

    output_file = output_file.replace(".mkv", ".mp4")
    
    # Force overwrite: remove output_file if it exists.
    if os.path.exists(output_file):
        os.remove(output_file)
        
    cmd = [
        'ffmpeg',
        '-y',                           # Overwrite output file
        '-loglevel', 'info',             # Verbosity
        '-nostdin',                     # Disable stdin
        '-i', input_file,                # Input file
        '-c:v', 'libx264',               # Video codec: H.264
        '-preset', 'slow',               # Slower = better compression efficiency
        '-crf', '5',                    # Constant Rate Factor (lower = better quality; 16 is visually lossless)
        '-pix_fmt', 'yuv420p',            # Pixel format (safe for most players)
        '-c:a', 'copy',                  # Copy audio without re-encoding (if you don't need to compress it)
        output_file                      # Output file
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
    upload_url = "https://signcollect.nl/videoProc/upload_tyd.php"
    
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

def process_video_file(input_file, output_file, temp_dir=None, original_filename=None):
    global GLOBAL_HEAD, GLOBAL_WAIST, GLOBAL_MIDPOINT_X, GLOBAL_NOSE_X, GLOBAL_NOSE_Y, GLOBAL_ROTATION_ANGLE, GLOBAL_IMG_HEIGHT, GLOBAL_IMG_WIDTH
    global GLOBAL_HEAD_NEW, GLOBAL_WAIST_NEW, GLOBAL_NOSE_NEW, GLOBAL_TOP_NEW, GLOBAL_WAIST_NEW, GLOBAL_NEW_LEFT_NEW, GLOBAL_NEW_RIGHT_NEW
    
    try:
        print(f"Processing file: {input_file}")
        
        # Create a unique temp directory for this task if not provided
        if temp_dir is None:
            temp_dir = os.path.join("/Users/signlab/drs/temp/", f"task_{uuid.uuid4().hex}")
            os.makedirs(temp_dir, exist_ok=True)
        
        # Extract frames to disk
        frames_dir = os.path.join(temp_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        frame_paths, _ = extract_frames(input_file, frames_dir)
        
        if not frame_paths:
            print(f"No frames extracted from {input_file}. Skipping.")
            cleanup_temp_dirs([temp_dir])
            return False
        
        # Get video properties
        cap = cv2.VideoCapture(input_file)
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        
        # Prepare directory for processed frames
        processed_dir = os.path.join(temp_dir, "processed")
        os.makedirs(processed_dir, exist_ok=True)
        
        # Reset global variables before processing new video
        GLOBAL_HEAD = None
        GLOBAL_WAIST = None
        GLOBAL_MIDPOINT_X = None
        GLOBAL_NOSE_X = None
        GLOBAL_NOSE_Y = None
        GLOBAL_ROTATION_ANGLE = None
        GLOBAL_IMG_HEIGHT = None
        GLOBAL_IMG_WIDTH = None
        GLOBAL_HEAD_NEW = None
        GLOBAL_WAIST_NEW = None
        GLOBAL_NOSE_NEW = None
        GLOBAL_TOP_NEW = None
        GLOBAL_WAIST_NEW = None
        GLOBAL_NEW_LEFT_NEW = None
        GLOBAL_NEW_RIGHT_NEW = None
        
        processed_paths = []
        
        # Process all frames using the same detection parameters from first frame
        for idx, frame_path in enumerate(frame_paths):
            output_path = os.path.join(processed_dir, f"processed_{idx:06d}.png")
            is_first_frame = (idx == 0)
            
            # Process the frame (only first frame does detection)
            process_single_image(frame_path, output_path, is_first_frame)
            processed_paths.append(output_path)
        
        # Bundle processed frames into a video
        if not write_video(processed_paths, output_file, fps):
            print(f"Failed to write video from processed frames for {input_file}")
            cleanup_temp_dirs([temp_dir])
            return False
        
        # Re-encode with ffmpeg using h264
        reencoded_file = reencode_with_ffmpeg(output_file)

        if reencoded_file:
            # Upload the re-encoded video
            if upload_video(reencoded_file):
                print(f"Upload successful for {reencoded_file}")
                return True
            else:
                print(f"Upload failed for {reencoded_file}")
                return False
        
        return True
    except Exception as e:
        print(f"Error processing {input_file}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Always clean up temp directory when done, whether successful or not
        cleanup_temp_dirs([temp_dir])


def get_head_coords(image):
    """
    Get head coordinates from the image using pose detection.
    Returns the y-coordinate of the head.
    """
    mp_pose = mp.solutions.pose
    with mp_pose.Pose(static_image_mode=True, min_detection_confidence=0.5) as pose:
        img_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        results = pose.process(img_rgb)
        if not results.pose_landmarks:
            print("Warning: No pose landmarks detected.")
            return None
        lm = results.pose_landmarks.landmark
        head_y = lm[mp_pose.PoseLandmark.NOSE].y * image.shape[0]
        return int(head_y) - 300

def get_waist_coords(image):
    """
    Get waist coordinates from the image using pose detection.
    Returns the y-coordinate of the waist.
    """
    mp_pose = mp.solutions.pose
    with mp_pose.Pose(static_image_mode=True, min_detection_confidence=0.5) as pose:
        img_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        results = pose.process(img_rgb)
        if not results.pose_landmarks:
            print("Warning: No pose landmarks detected.")
            return None
        lm = results.pose_landmarks.landmark
        left_hip_y = lm[mp_pose.PoseLandmark.LEFT_HIP].y * image.shape[0]
        right_hip_y = lm[mp_pose.PoseLandmark.RIGHT_HIP].y * image.shape[0]
        waist_y = int((left_hip_y + right_hip_y) / 2)
        return int(waist_y)
    
def get_nose_coords(image):
    """
    Get nose coordinates from the image using pose detection.
    Returns the x-coordinate of the nose.
    """
    mp_pose = mp.solutions.pose
    with mp_pose.Pose(static_image_mode=True, min_detection_confidence=0.5) as pose:
        img_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        results = pose.process(img_rgb)
        if not results.pose_landmarks:
            print("Warning: No pose landmarks detected.")
            return None
        lm = results.pose_landmarks.landmark
        nose_x = lm[mp_pose.PoseLandmark.NOSE].x * image.shape[1]  # Use width (shape[1]) instead of height (shape[0])
        return int(nose_x)

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

def estimate_bbox(image, conf_threshold=0.25):
    """
    Use YOLOv8 to detect a person in the image and return their bounding box.
    Returns (top, bottom, left, right) coordinates.
    """
    # Load YOLOv8 model
    model = YOLO("yolov8n.pt")  # Load the YOLOv8 model
    
    # Convert BGR to RGB (YOLO expects RGB format)
    rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    
    # Run detection on the image with lower confidence threshold
    results = model(rgb_image, conf=conf_threshold)
    
    # Get image dimensions for fallback
    height, width = image.shape[:2]
    
    # Initialize with full image as fallback
    top, left = 0, 0
    bottom, right = height, width
    
    # Look for person detections
    person_detected = False
    max_area = 0
    
    # Debug: Print all detections
    print(f"YOLOv8 found {len(results[0].boxes)} objects")
    
    for result in results:
        boxes = result.boxes
        print(f"Classes detected: {boxes.cls.tolist()}")
        print(f"Confidences: {boxes.conf.tolist()}")
        
        # Iterate through detections
        for i, (box, cls, conf) in enumerate(zip(boxes.xyxy, boxes.cls, boxes.conf)):
            # Check if the detected object is a person (class 0)
            if int(cls) == 0:  # Person class is typically 0 in COCO dataset used by YOLOv8
                x1, y1, x2, y2 = box.tolist()
                x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
                
                print(f"Person detected: Confidence={conf:.2f}, Box=[{x1}, {y1}, {x2}, {y2}]")
                
                # Calculate area of bounding box
                area = (x2 - x1) * (y2 - y1)
                
                # Keep the largest person detection
                if area > max_area:
                    max_area = area
                    top, left = y1, x1
                    bottom, right = y2, x2
                    person_detected = True
    
    if person_detected:
        # Add margins (10% on each side)
        margin_x = int((right - left) * 0.01)
        margin_y = int((bottom - top) * 0.01)
        
        top = max(0, top - margin_y)
        bottom = min(height, bottom + margin_y)
        left = max(0, left - margin_x)
        right = min(width, right + margin_x)
        
        print(f"Person detected with bbox: top={top}, bottom={bottom}, left={left}, right={right}")
        
        # Save debug visualization
        debug_img = image.copy()
        cv2.rectangle(debug_img, (left, top), (right, bottom), (0, 255, 0), 2)
        debug_path = "/Users/signlab/drs/temp/yolo_detection.jpg"
        cv2.imwrite(debug_path, debug_img)
        print(f"Detection visualization saved to {debug_path}")
    else:
        print("No person detected, using full image")
        # Try again with lower threshold
        if conf_threshold > 0.1:
            print("Retrying with lower confidence threshold...")
            return estimate_bbox(image, conf_threshold=0.1)
    
    return top, bottom, left, right

def process_single_image(image_path, output_path, is_first_frame=True, debug=False):
    """
    Processes a single image: detects pose, calculates rotation, crops vertically and horizontally,
    resizes while maintaining aspect ratio, and centers the result on a 1920x1080 canvas.
    
    When is_first_frame is True, it performs pose detection.
    Otherwise, it reuses the global variables set by the first frame.
    """
    global GLOBAL_HEAD, GLOBAL_WAIST, GLOBAL_MIDPOINT_X, GLOBAL_NOSE_X, GLOBAL_NOSE_Y, GLOBAL_ROTATION_ANGLE, GLOBAL_IMG_HEIGHT, GLOBAL_IMG_WIDTH, GLOBAL_HEAD_NEW, GLOBAL_WAIST_NEW, GLOBAL_NOSE_NEW, GLOBAL_TOP_NEW, GLOBAL_NEW_RIGHT_NEW, GLOBAL_NEW_LEFT_NEW
    # print(f"Processing {'first frame' if is_first_frame else 'frame'}: {image_path}")

    # Read the image
    image = cv2.imread(image_path)
    if image is None:
        print(f"Error: Failed to read image {image_path}.")
        return
        
    img_height, img_width = image.shape[:2]

    #print image dimensions
    # print("Image dimensions:", img_height, img_width)

    if is_first_frame:
        # Only perform pose detection for first frame
        head, waist, frame_shape, midpoint_x, _, _, _, _, _, nose_x, nose_y = get_vertical_bbox(image_path)

        if frame_shape is None:
            print(f"Skipping processing for {image_path} due to read error.")
            return

        # Handle cases where pose detection might fail or return None
        if head is None or waist is None or midpoint_x is None or nose_x is None or nose_y is None:
            print(f"Warning: Could not get all required coordinates for {image_path}. Using defaults.")
            head = 0
            waist = img_height
            midpoint_x = img_width // 2
            nose_x = midpoint_x
            nose_y = img_height // 3
            rotation_angle = 0
        else:
            # Calculate Rotation Angle
            pivot_y = img_height
            dx = nose_x - midpoint_x
            vertical_distance = pivot_y - nose_y

            # Avoid division by zero
            if abs(vertical_distance) < 1e-6:
                angle_rad = math.pi / 2 if dx > 0 else (-math.pi / 2 if dx < 0 else 0)
            else:
                angle_rad = math.atan2(dx, vertical_distance)

            rotation_angle = math.degrees(angle_rad)
        
        # Store in global variables for reuse with subsequent frames
        GLOBAL_HEAD = head
        GLOBAL_WAIST = waist
        GLOBAL_MIDPOINT_X = midpoint_x
        GLOBAL_NOSE_X = nose_x
        GLOBAL_NOSE_Y = nose_y
        GLOBAL_ROTATION_ANGLE = rotation_angle
        GLOBAL_IMG_HEIGHT = img_height
        GLOBAL_IMG_WIDTH = img_width
    else:
        # Reuse detection results from first frame
        head = GLOBAL_HEAD
        waist = GLOBAL_WAIST
        midpoint_x = GLOBAL_MIDPOINT_X
        nose_x = GLOBAL_NOSE_X
        nose_y = GLOBAL_NOSE_Y
        rotation_angle = GLOBAL_ROTATION_ANGLE

        
        # Adjust coordinates if current frame has different dimensions
        if img_height != GLOBAL_IMG_HEIGHT or img_width != GLOBAL_IMG_WIDTH:
            height_ratio = img_height / GLOBAL_IMG_HEIGHT
            width_ratio = img_width / GLOBAL_IMG_WIDTH
            head = int(head * height_ratio)
            waist = int(waist * height_ratio)
            midpoint_x = int(midpoint_x * width_ratio)
            nose_x = int(nose_x * width_ratio)
            nose_y = int(nose_y * height_ratio)

    # Process the image using determined parameters
    processed_image = image.copy()

    # Apply rotation
    rotation_matrix = cv2.getRotationMatrix2D((midpoint_x, img_height), rotation_angle, 1.0)
    processed_image = cv2.warpAffine(processed_image, rotation_matrix, (img_width, img_height),
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    

    #save the image if debug is true
    if debug:
        debug_image_path = os.path.join(os.path.dirname(output_path), "firststep_debug_image.jpg")
        cv2.imwrite(debug_image_path, processed_image)
        print(f"Debug image saved at {debug_image_path}")


    if is_first_frame:
        #now we want to use yolo8 bbox to estimate the bbox of the subject on image
        top, down, left, right = estimate_bbox(processed_image)
        print("Top:", top, "Down:", down, "Left:", left, "Right:", right)

        #get midpoint from the left and right
        # midpoint_x = (left + right) // 2
        
        #now we want to get waist from processed_image
        waist = get_waist_coords(processed_image)
        if waist is None:
            waist = down  # Fall back to YOLO bounding box if waist detection fails

        #then we are going to add 10% margin to the top
        margin_top = top * 0.1
        top = int(top - margin_top)

        #then we are going to calculate height between top and waist
        height = waist - top


        #now we have the complete heigt and we want to calculate 16:9 dimension of the width based on the height
        target_width = int(height * 1.77)
        print("Target width:", target_width)    
        #get top:waist and height
        #then we are going to calculate the new left and right based on the target width
        new_left = max(0, midpoint_x - target_width // 2)
        new_right = min(processed_image.shape[1], new_left + target_width)
        #if new_right is maxed out, then adjust new_left accordingly
        if new_right == processed_image.shape[1]:
            #calculate new_left with midpoint_x and adjust new difference with new_right
            new_left = midpoint_x - (new_right - midpoint_x) + 50
        #if new_left is maxed out, then adjust new_right accordingly
        if new_left == 0:
            #calculate new_left with midpoint_x and adjust new difference with new_right
            new_right = midpoint_x + (midpoint_x - new_left) - 50

        #print image dimensions after resizing
        print("Image dimensions after resizing:", processed_image.shape[0], processed_image.shape[1])
        #put a line in the middle and save the image if debug is true


        #pass local vars to global vars
        GLOBAL_TOP_NEW = top
        GLOBAL_WAIST_NEW = waist
        GLOBAL_NEW_LEFT_NEW = new_left
        GLOBAL_NEW_RIGHT_NEW = new_right

    # Make sure global variables have valid values before using them
    if GLOBAL_TOP_NEW is None or GLOBAL_WAIST_NEW is None or GLOBAL_NEW_LEFT_NEW is None or GLOBAL_NEW_RIGHT_NEW is None:
        # If global variables are not set (could happen if first frame processing failed),
        # use fallback values to crop the entire frame
        print(f"Warning: Global bounding box variables not set. Using fallback crop for {image_path}")
        GLOBAL_TOP_NEW = 0
        GLOBAL_WAIST_NEW = img_height
        GLOBAL_NEW_LEFT_NEW = 0
        GLOBAL_NEW_RIGHT_NEW = img_width
    
    # Ensure we don't try to crop outside image bounds
    top = max(0, min(GLOBAL_TOP_NEW, img_height-1))
    waist = max(top+1, min(GLOBAL_WAIST_NEW, img_height))
    left = max(0, min(GLOBAL_NEW_LEFT_NEW, img_width-1))
    right = max(left+1, min(GLOBAL_NEW_RIGHT_NEW, img_width))

    #we are going to move the image with 500 pixels to right

    processed_image = processed_image[top:waist, left:right]
    processed_image = np.roll(processed_image, 500, axis=1)

    

    # Resize to 1920x1080
    processed_image = cv2.resize(processed_image, (1920, 1080), interpolation=cv2.INTER_AREA)

    if debug:
        cv2.line(processed_image, (960, 0), (960, 1080), (255, 0, 0), 2)
        cv2.line(processed_image, (0, 540), (1920, 540), (255, 0, 0), 2)
        #save the image
        cv2.imwrite("/Users/signlab/drs/temp/debug_image.jpg", processed_image)

    #12. Draw a triangle on the top left corner, fill the triangle with white color and no borders
    triangle_points = np.array([[0, 0], [200, 0], [0, 300]], np.int32)
    cv2.fillPoly(processed_image, [triangle_points], (255, 255, 255))


    #13. paste png image on the left right corner of canvas
    logo = cv2.imread("/Users/signlab/drs/tyd_logo.png", cv2.IMREAD_UNCHANGED)
    #paste the logo on canvas on the top left corner
    if logo is not None and logo.shape[2] == 4:
        b, g, r, a = cv2.split(logo)
        
        # Create a white background for the logo (all values 255)
        white_bg = np.ones_like(a, dtype=logo.dtype) * 255
        
        # Normalize the alpha channel to be in the range 0.0-1.0
        alpha = a.astype(float) / 255.0
        
        # Blend each color channel with the white background:
        # blended = logo_color * alpha + white * (1 - alpha)
        b = b.astype(float) * alpha + white_bg.astype(float) * (1 - alpha)
        g = g.astype(float) * alpha + white_bg.astype(float) * (1 - alpha)
        r = r.astype(float) * alpha + white_bg.astype(float) * (1 - alpha)
        
        # Merge the blended channels and convert back to uint8
        logo = cv2.merge([b.astype(np.uint8), g.astype(np.uint8), r.astype(np.uint8)])
        logo_h, logo_w = logo.shape[:2]
        # Resize logo to fit within 200x200
        logo_scale = min(100 / logo_w, 100 / logo_h)
        new_logo_w = int(logo_w * logo_scale)
        new_logo_h = int(logo_h * logo_scale)
        resized_logo = cv2.resize(logo, (new_logo_w, new_logo_h), interpolation=cv2.INTER_AREA)

        # Calculate paste position for the logo on the left corner
        paste_x = 25
        paste_y = 25

        # Paste the resized logo onto the canvas
        processed_image[paste_y:paste_y + new_logo_h, paste_x:paste_x + new_logo_w] = resized_logo
    # Save processed image
    cv2.imwrite(output_path, processed_image)

    #when parameter debug is true then save the image as processed_image and debug_image
    if debug:
        print("Height and width are", processed_image.shape[0], processed_image.shape[1])
        debug_image_path = os.path.join(os.path.dirname(output_path), "debug_image.jpg")
        cv2.imwrite(debug_image_path, processed_image)
        print(f"Debug image saved at {debug_image_path}")

    return processed_image
# ----- Directory Loop -----

def main():
    # Initialize the API client
    api_client = VideoAPIClient('https://signcollect.nl/renderServer')
    
    while True:
        try:
            # Get videos that need TYD rendering (non-cropped)
            response = api_client.get_tyd_rendered_noncropped()
            
            # Ensure we have a list to work with
            videos_to_process = []
            if isinstance(response, list):
                videos_to_process = response
            elif isinstance(response, dict) and "error" not in response:
                # If response is a dict but not an error, try to extract list
                for key, value in response.items():
                    if isinstance(value, list):
                        videos_to_process = value
                        break
            
            # Print debug info
            print(f"API Response type: {type(response).__name__}")
            
            if not videos_to_process:
                print("No videos to process. Waiting for 5 minutes before checking again...")
                time.sleep(300)  # 5 minutes
                continue
                
            print(f"Found {len(videos_to_process)} videos to process")
            
            # Limit to 200 videos at a time - now safely slicing a list
            videos_batch = videos_to_process[:200]
            
            tasks = []
            
            for video in videos_batch:
                # Get the filename from the API response
                if not isinstance(video, dict) or 'm_file' not in video:
                    print(f"Invalid video entry, skipping: {video}")
                    continue
                    
                filename = video['m_file']
                
                # Extract date from filename (format: M20250429_7926.wav)
                if len(filename) >= 9 and filename[0] == 'M' and filename[1:9].isdigit():
                    date_str = filename[1:9]  # Extract "20250429"
                    year = date_str[0:4]      # "2025"
                    month = date_str[4:6]     # "04"
                    day = date_str[6:8]       # "29"
                    
                    # Format the date directory
                    date_dir = f"{year}-{month}-{day}"
                    
                    # Construct input and output paths
                    input_folder = f"/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles/{date_dir}/tyd_noncropped"
                    output_folder = f"/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles/{date_dir}/tyd_post"
                    
                    # Create output folder if it doesn't exist
                    if not os.path.exists(output_folder):
                        os.makedirs(output_folder, exist_ok=True)
                    
                    # Convert .wav extension to .mkv for input file
                    input_filename = filename.replace('.wav', '.mkv')
                    input_file = os.path.join(input_folder, input_filename)
                    output_file = os.path.join(output_folder, input_filename)
                    
                    # Check if input file exists
                    if not os.path.exists(input_file):
                        print(f"Input file not found: {input_file}. Skipping.")
                        continue
                    
                    # Check if output file already exists
                    if os.path.exists(output_file):
                        print(f"Output file already exists for {input_filename}. Skipping.")
                        # Mark as processed in the API
                        api_client.update_tyd_rendered_cropped(filename, 'm_file')
                        continue
                    
                    # Check file size (skip if larger than 1000MB)
                    if os.path.getsize(input_file) > 1000 * 1024 * 1024:
                        print(f"Skipping {input_filename} because it is larger than 1000MB.")
                        continue
                    
                    # Determine shift direction based on first character of filename
                    first_char = filename[0].upper()
                    if first_char == 'L':
                        shift_direction = 'L'
                    elif first_char == 'R':
                        shift_direction = 'R'
                    else:
                        shift_direction = 'M'
                    
                    # Add to tasks
                    tasks.append((input_file, output_file, shift_direction, filename))
                else:
                    print(f"Invalid filename format: {filename}. Skipping.")
            
            if tasks:
                with ProcessPoolExecutor(max_workers=5) as executor:
                    futures_to_data = {}
                    
                    # Create a unique temp directory for each task and submit
                    for task in tasks:
                        input_file, output_file, shift_direction, original_filename = task
                        task_temp_dir = os.path.join("/Users/signlab/drs/temp/", f"task_{uuid.uuid4().hex}")
                        os.makedirs(task_temp_dir, exist_ok=True)
                        
                        # Submit task with temp directory
                        future = executor.submit(process_video_file, input_file, output_file, task_temp_dir, original_filename)
                        futures_to_data[future] = (task_temp_dir, original_filename)
                    
                    # Process completed futures
                    for future in as_completed(futures_to_data.keys()):
                        temp_dir, original_filename = futures_to_data[future]
                        try:
                            result = future.result()
                            if result:
                                # Success - Mark as processed in the API
                                api_response = api_client.update_tyd_rendered_cropped(original_filename, 'm_file')
                                print(f"Marked {original_filename} as processed in API. Response: {api_response}")
                            else:
                                print(f"Processing failed for {original_filename}, not updating API status")
                        except Exception as e:
                            print(f"Task execution failed for {original_filename}: {e}")
                        finally:
                            # Extra cleanup in case the process_video_file didn't clean up
                            if os.path.exists(temp_dir):
                                cleanup_temp_dirs([temp_dir])
            else:
                print("No valid tasks to process.")
                time.sleep(300)  # Wait 5 minutes before checking again
            
            # After processing a batch, wait a short time to avoid hammering the API
            time.sleep(10)
            
        except Exception as e:
            print(f"Error in main loop: {e}")
            import traceback
            traceback.print_exc()
            print("Waiting 5 minutes before retrying...")
            time.sleep(300)  # 5 minutes

if __name__ == "__main__":
    # First empty the temp directory and clean up old files
    temp_dir = "/Users/signlab/drs/temp/"
    if os.path.exists(temp_dir):
        print("Cleaning up temp directory before starting...")
        cleanup_old_temp_files(temp_dir, max_age_hours=0)  # Clean all old files
        cleanup_temp_by_size(temp_dir, max_size_gb=10)  # Ensure size is under limit
    else:
        os.makedirs(temp_dir, exist_ok=True)
    
    main()
    # first empty the temp directory
    # temp_dir = "/Users/signlab/drs/temp/"
    # if os.path.exists(temp_dir):
    #     shutil.rmtree(temp_dir)
    #     os.makedirs(temp_dir, exist_ok=True)
        
    # Run main() in an infinite loop every hour if it's not currently running.
    while True:
        try:
            # Clean up old temp files before each run
            cleanup_old_temp_files(temp_dir, max_age_hours=1)
            cleanup_temp_by_size(temp_dir, max_size_gb=10)
            
            main()
        except Exception as e:
            print(f"Error in main execution: {e}")
            
        # Clean up after processing
        cleanup_old_temp_files(temp_dir, max_age_hours=1)
        
        print("Sleeping for one hour before next run...")
        if tasks_temp > 0:
            time.sleep(10)
        else:
            time.sleep(3600)


   # Define input and output paths for single image processing
    # input_image = "vlcsnap-2025-04-29-19h37m13s431.png"  # Make sure this image exists in the same directory or provide full path
    # output_image = "processed_image.jpg"

    # # # # Check if input image exists
    # if os.path.exists(input_image):
    #     process_single_image(input_image, output_image, is_first_frame=True, debug=True)
    # else:
    #     print(f"Error: Input image not found at {input_image}")

    # Example of how video processing could still be called (optional)
    # input_video = "M20250407_7050.mkv"
    # output_video = input_video.replace(".mkv", "_processed.mp4")
    # if os.path.exists(input_video):   
    #     task_temp_dir = os.path.join("/Users/signlab/drs/temp/", f"task_{uuid.uuid4().hex}")

    #     process_video_file(input_video, output_video, task_temp_dir)
    # else:
    #     print(f"Error: Input video not found at {input_video}")