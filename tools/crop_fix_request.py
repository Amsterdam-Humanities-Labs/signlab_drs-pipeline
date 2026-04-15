#!/usr/bin/python3
"""
crop_fix.py - Batch re-crop with fixed dimensions from reference video

This script:
1. Analyzes M20251201_7047.mp4 to determine crop dimensions based on hand positions
2. Applies those exact same dimensions to all 10 specified videos
3. Overwrites existing files in post/ folder
4. Uploads processed videos to signcollect API
"""

import os
import cv2
import mediapipe as mp
import numpy as np
import time
from datetime import datetime
import json
import subprocess
import requests
import tempfile
import shutil
import uuid
import math
import sys

# Ensure output is flushed immediately to logs
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ===== CONFIGURATION =====

TARGET_FILES = [
    "M20251201_7043.mp4",
    "M20251201_7044.mp4",
    "M20251201_7045.mp4",
    "M20251201_7047.mp4",
    "M20251201_7048.mp4",
    "M20251201_7052.mp4",
    "M20251201_7053.mp4",
    "M20251201_7055.mp4",
    "M20251201_7056.mp4",
    "M20251201_7057.mp4",
]

REFERENCE_FILE = "M20251201_7047.mp4"
BASE_DIR = "/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles/2025-12-01"
TEMP_DIR = "/Users/signlab/drs/temp/"

# ===== HELPER FUNCTIONS =====

def get_video_orientation(filepath):
    """Check if a video is portrait or landscape using mediainfo"""
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
        print(f"Error determining orientation: {e}")
        return 0, "unknown"


def extract_frames(video_path, output_dir=None):
    """Extract frames from video and save to disk instead of memory"""
    if output_dir is None:
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

    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return frame_paths, output_dir, fps


def get_vertical_bbox(frame_path):
    """Detect pose landmarks using MediaPipe"""
    mp_pose = mp.solutions.pose
    frame = cv2.imread(frame_path)
    with mp_pose.Pose(static_image_mode=True, min_detection_confidence=0.5) as pose:
        img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = pose.process(img_rgb)
        if not results.pose_landmarks:
            return None

        lm = results.pose_landmarks.landmark
        head_top = min(lm[mp_pose.PoseLandmark.LEFT_EYE].y,
                       lm[mp_pose.PoseLandmark.RIGHT_EYE].y,
                       lm[mp_pose.PoseLandmark.LEFT_EAR].y,
                       lm[mp_pose.PoseLandmark.RIGHT_EAR].y) * frame.shape[0]

        # Calculate hip midpoint coordinates
        left_hip_x = lm[mp_pose.PoseLandmark.LEFT_HIP].x * frame.shape[1]
        left_hip_y = lm[mp_pose.PoseLandmark.LEFT_HIP].y * frame.shape[0]
        right_hip_x = lm[mp_pose.PoseLandmark.RIGHT_HIP].x * frame.shape[1]
        right_hip_y = lm[mp_pose.PoseLandmark.RIGHT_HIP].y * frame.shape[0]

        # Calculate position of nose
        nose_x = lm[mp_pose.PoseLandmark.NOSE].x * frame.shape[1]
        nose_y = lm[mp_pose.PoseLandmark.NOSE].y * frame.shape[0]

        # Midpoint calculation
        midpoint_x = int((left_hip_x + right_hip_x) / 2)
        waist_y = int((left_hip_y + right_hip_y) / 2)

        head_top = head_top - 300  # Extra margin above head

        return {
            'head_top': int(head_top),
            'waist_y': waist_y,
            'frame_shape': frame.shape,
            'midpoint_x': midpoint_x,
            'nose_x': nose_x,
            'nose_y': nose_y
        }


def get_hand_landmarks(frame_path):
    """Detect hand landmarks using MediaPipe Hands"""
    mp_hands = mp.solutions.hands
    frame = cv2.imread(frame_path)

    all_hand_landmarks = []

    with mp_hands.Hands(
        static_image_mode=True,
        max_num_hands=2,
        min_detection_confidence=0.5
    ) as hands:
        img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = hands.process(img_rgb)

        if results.multi_hand_landmarks:
            for hand_landmarks in results.multi_hand_landmarks:
                for landmark in hand_landmarks.landmark:
                    x_px = landmark.x * frame.shape[1]
                    y_px = landmark.y * frame.shape[0]
                    all_hand_landmarks.append((x_px, y_px))

    return all_hand_landmarks, frame.shape


def calculate_boundary_expansion(initial_bounds, hand_landmarks, frame_shape, margin=50):
    """Calculate crop boundaries to include all fingers with guaranteed margin"""
    if not hand_landmarks:
        return initial_bounds

    x_positions = [x for x, y in hand_landmarks]
    y_positions = [y for x, y in hand_landmarks]

    min_x = min(x_positions)
    max_x = max(x_positions)
    min_y = min(y_positions)
    max_y = max(y_positions)

    hand_left = max(0, min_x - margin)
    hand_right = min(frame_shape[1], max_x + margin)
    hand_top = max(0, min_y - margin)
    hand_bottom = min(frame_shape[0], max_y + margin)

    expanded = {
        'top': min(initial_bounds['top'], hand_top),
        'bottom': max(initial_bounds['bottom'], hand_bottom),
        'left': min(initial_bounds['left'], hand_left),
        'right': max(initial_bounds['right'], hand_right)
    }

    return expanded


def write_video(frame_paths, output_path, fps):
    """Write video using frames saved on disk"""
    if not frame_paths:
        print("No frames to write to video.")
        return

    first_frame = cv2.imread(frame_paths[0])
    h, w = first_frame.shape[:2]

    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_path, fourcc, fps, (w, h))

    for frame_path in frame_paths:
        frame = cv2.imread(frame_path)
        out.write(frame)
        del frame

    out.release()


def reencode_with_ffmpeg(input_file, output_file=None):
    """Re-encode video using ffmpeg with h264 codec"""
    if output_file is None:
        file_parts = os.path.splitext(input_file)
        output_file = f"{file_parts[0]}_h264{file_parts[1]}"

    # Remove output file if it exists
    if os.path.exists(output_file):
        os.remove(output_file)

    cmd = [
        "/opt/homebrew/bin/ffmpeg", "-y", "-i", input_file,
        "-c:v", "libx264", "-preset", "medium",
        "-crf", "18", "-c:a", "copy",
        output_file
    ]

    print(f"Re-encoding video with ffmpeg: {input_file}")
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

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

        files = {'video': (filename, open(video_path, 'rb'), 'video/mp4')}
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


def cleanup_temp_dirs(dirs):
    """Clean up temporary directories"""
    for dir_path in dirs:
        if dir_path and os.path.exists(dir_path):
            try:
                shutil.rmtree(dir_path)
                print(f"Cleaned up: {dir_path}")
            except Exception as e:
                print(f"Error cleaning up {dir_path}: {e}")


def find_file_case_insensitive(directory, filename):
    """Find file with case-insensitive matching (handles .MP4 vs .mp4)"""
    base_name = os.path.splitext(filename)[0]

    for f in os.listdir(directory):
        f_base = os.path.splitext(f)[0]
        if f_base.upper() == base_name.upper() and f.lower().endswith(('.mp4', '.mov', '.avi')):
            return os.path.join(directory, f)

    return None


# ===== MAIN FUNCTIONS =====

def get_reference_dimensions(reference_path):
    """
    Analyze reference video and return fixed crop parameters.
    Returns dict with rotation_angle, crop bounds, and rotation center.
    """
    print(f"\n{'='*60}")
    print(f"ANALYZING REFERENCE VIDEO")
    print(f"{'='*60}")
    print(f"Reference file: {reference_path}")

    # Create temp directory for this analysis
    temp_dir = os.path.join(TEMP_DIR, f"ref_{uuid.uuid4().hex}")
    os.makedirs(temp_dir, exist_ok=True)

    try:
        # Extract frames
        print("Extracting frames from reference video...")
        frames_dir = os.path.join(temp_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        frame_paths, _, fps = extract_frames(reference_path, frames_dir)

        if not frame_paths:
            raise ValueError("No frames extracted from reference video")

        print(f"Extracted {len(frame_paths)} frames at {fps} fps")

        # Get pose landmarks from first frame
        print("Detecting pose landmarks...")
        pose_data = get_vertical_bbox(frame_paths[0])

        if pose_data is None:
            raise ValueError("No pose detected in reference video")

        head = pose_data['head_top']
        waist = pose_data['waist_y']
        frame_shape = pose_data['frame_shape']
        midpoint_x = pose_data['midpoint_x']
        nose_x = pose_data['nose_x']
        nose_y = pose_data['nose_y']

        print(f"Pose detected - Head: {head}, Waist: {waist}, Midpoint X: {midpoint_x}")

        # Calculate rotation angle
        midpoint_y = frame_shape[0]
        dx = nose_x - midpoint_x
        vertical_distance = midpoint_y - nose_y
        angle_rad = math.atan2(dx, vertical_distance)
        rotation_angle = math.degrees(angle_rad)

        print(f"Rotation angle: {rotation_angle:.2f} degrees")

        # Detect hand landmarks (skip first 30 and last 60 frames for resting pose)
        print("Detecting hand landmarks across frames...")
        skip_start = 30
        skip_end = 60
        total_frames = len(frame_paths)

        if total_frames > (skip_start + skip_end + 10):
            frames_to_analyze = frame_paths[skip_start:-skip_end]
        else:
            frames_to_analyze = frame_paths

        all_hand_landmarks = []
        for idx, frame_path in enumerate(frames_to_analyze):
            hand_landmarks, _ = get_hand_landmarks(frame_path)
            all_hand_landmarks.extend(hand_landmarks)

            if idx % 30 == 0:
                print(f"  Analyzed frame {idx}/{len(frames_to_analyze)}, total landmarks: {len(all_hand_landmarks)}")

        print(f"Detected {len(all_hand_landmarks)} hand landmarks total")

        # Apply rotation to hand landmarks
        rotation_matrix = cv2.getRotationMatrix2D((midpoint_x, midpoint_y), rotation_angle, 1.0)
        rotated_hand_landmarks = []

        for x, y in all_hand_landmarks:
            point = np.array([[[x, y]]], dtype=np.float32)
            rotated_point = cv2.transform(point, rotation_matrix)
            rotated_x = rotated_point[0][0][0]
            rotated_y = rotated_point[0][0][1]
            rotated_hand_landmarks.append((rotated_x, rotated_y))

        # Calculate initial crop boundaries
        crop_width = 1440
        half_crop = crop_width // 2
        current_width = frame_shape[1]
        target_height = waist - head
        target_width = int(target_height * 1.15)

        initial_left = max(0, midpoint_x - half_crop)
        initial_right = min(current_width, initial_left + crop_width)

        if initial_right == current_width:
            initial_left = max(0, current_width - crop_width)

        if initial_right - initial_left < target_width:
            initial_left = max(0, midpoint_x - target_width // 2)
            initial_right = min(current_width, initial_left + target_width)

        if initial_left == 0:
            initial_right = midpoint_x + (midpoint_x - initial_left) - 50
        if initial_right == current_width:
            initial_left = midpoint_x - (initial_right - midpoint_x) + 50

        initial_bounds = {
            'top': head,
            'bottom': waist,
            'left': initial_left,
            'right': initial_right
        }

        # Expand bounds for hand landmarks
        expanded_bounds = calculate_boundary_expansion(
            initial_bounds,
            rotated_hand_landmarks,
            frame_shape,
            margin=50
        )

        # Calculate reference person height (used for scaling other videos)
        ref_person_height = waist - head

        final_params = {
            'rotation_angle': rotation_angle,
            'midpoint_x': midpoint_x,
            'midpoint_y': midpoint_y,
            'final_top': int(expanded_bounds['top']),
            'final_bottom': int(expanded_bounds['bottom']),
            'final_left': int(expanded_bounds['left']),
            'final_right': int(expanded_bounds['right']),
            'frame_shape': frame_shape,
            'ref_person_height': ref_person_height,  # For scaling other videos to match
        }

        print(f"\n{'='*60}")
        print("FINAL CROP PARAMETERS FROM REFERENCE:")
        print(f"  Rotation angle: {final_params['rotation_angle']:.2f} degrees")
        print(f"  Rotation center: ({final_params['midpoint_x']}, {final_params['midpoint_y']})")
        print(f"  Crop bounds - Top: {final_params['final_top']}, Bottom: {final_params['final_bottom']}")
        print(f"  Crop bounds - Left: {final_params['final_left']}, Right: {final_params['final_right']}")
        print(f"  Reference person height: {final_params['ref_person_height']} px")
        print(f"{'='*60}\n")

        return final_params

    finally:
        cleanup_temp_dirs([temp_dir])


def process_with_fixed_dimensions(input_file, output_file, crop_params):
    """
    Process a single video using pre-determined crop parameters.
    Scales the video so the person appears the same size as in the reference.
    """
    print(f"\n{'-'*60}")
    print(f"PROCESSING: {os.path.basename(input_file)}")
    print(f"{'-'*60}")

    temp_dir = os.path.join(TEMP_DIR, f"proc_{uuid.uuid4().hex}")
    os.makedirs(temp_dir, exist_ok=True)

    try:
        # Extract frames
        print("Extracting frames...")
        frames_dir = os.path.join(temp_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        frame_paths, _, fps = extract_frames(input_file, frames_dir)

        if not frame_paths:
            raise ValueError("No frames extracted")

        print(f"Extracted {len(frame_paths)} frames at {fps} fps")

        # Get crop parameters from reference
        rotation_angle = crop_params['rotation_angle']
        midpoint_x = crop_params['midpoint_x']
        midpoint_y = crop_params['midpoint_y']
        final_top = crop_params['final_top']
        final_bottom = crop_params['final_bottom']
        final_left = crop_params['final_left']
        final_right = crop_params['final_right']
        ref_person_height = crop_params['ref_person_height']

        # Detect person height in THIS video to calculate scale factor
        print("Detecting person size in target video...")
        target_pose = get_vertical_bbox(frame_paths[0])

        if target_pose is None:
            print("WARNING: No pose detected in target video, using scale factor 1.0")
            scale_factor = 1.0
        else:
            target_person_height = target_pose['waist_y'] - target_pose['head_top']
            scale_factor = ref_person_height / target_person_height
            print(f"  Reference person height: {ref_person_height} px")
            print(f"  Target person height: {target_person_height} px")
            print(f"  Scale factor: {scale_factor:.4f}")

        # Process frames with scaling
        print(f"Applying scale ({scale_factor:.4f}), rotation ({rotation_angle:.2f} deg) and crop...")
        processed_dir = os.path.join(temp_dir, "processed")
        os.makedirs(processed_dir, exist_ok=True)
        processed_paths = []

        # Calculate expected output dimensions (should be same as reference)
        output_height = final_bottom - final_top
        output_width = final_right - final_left

        for idx, frame_path in enumerate(frame_paths):
            frame = cv2.imread(frame_path)
            orig_h, orig_w = frame.shape[:2]

            # Step 1: Scale the frame to match reference person size
            if scale_factor != 1.0:
                new_w = int(orig_w * scale_factor)
                new_h = int(orig_h * scale_factor)
                scaled = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            else:
                scaled = frame
                new_w, new_h = orig_w, orig_h

            # Step 2: Adjust midpoint for rotation based on scale
            scaled_midpoint_x = int(midpoint_x * scale_factor)
            scaled_midpoint_y = int(midpoint_y * scale_factor)

            # Step 3: Apply rotation
            rotation_matrix = cv2.getRotationMatrix2D((scaled_midpoint_x, scaled_midpoint_y), rotation_angle, 1.0)
            rotated = cv2.warpAffine(scaled, rotation_matrix, (new_w, new_h))

            # Step 4: Adjust crop boundaries based on scale
            scaled_top = int(final_top * scale_factor)
            scaled_bottom = int(final_bottom * scale_factor)
            scaled_left = int(final_left * scale_factor)
            scaled_right = int(final_right * scale_factor)

            # Ensure crop boundaries are within frame
            scaled_top = max(0, scaled_top)
            scaled_bottom = min(new_h, scaled_bottom)
            scaled_left = max(0, scaled_left)
            scaled_right = min(new_w, scaled_right)

            # Step 5: Apply crop
            cropped = rotated[scaled_top:scaled_bottom, scaled_left:scaled_right]

            # Step 6: Resize to exact output dimensions to ensure consistency
            if cropped.shape[0] != output_height or cropped.shape[1] != output_width:
                cropped = cv2.resize(cropped, (output_width, output_height), interpolation=cv2.INTER_LINEAR)

            # Save processed frame
            output_frame_path = os.path.join(processed_dir, f"processed_{idx:06d}.jpg")
            cv2.imwrite(output_frame_path, cropped)
            processed_paths.append(output_frame_path)

            del frame, scaled, rotated, cropped

            if idx % 100 == 0:
                print(f"  Processed {idx}/{len(frame_paths)} frames")

        print(f"Processed all {len(processed_paths)} frames")

        # Write video
        print(f"Writing video to {output_file}...")
        write_video(processed_paths, output_file, fps)

        # Re-encode with ffmpeg
        h264_file = reencode_with_ffmpeg(output_file)

        if h264_file:
            # Upload to API
            upload_success = upload_video(h264_file)
            if upload_success:
                print(f"SUCCESS: {os.path.basename(input_file)}")
            else:
                print(f"WARNING: Upload failed for {os.path.basename(input_file)}")
        else:
            print(f"ERROR: Re-encoding failed for {os.path.basename(input_file)}")

        return True

    except Exception as e:
        print(f"ERROR processing {os.path.basename(input_file)}: {str(e)}")
        return False

    finally:
        cleanup_temp_dirs([temp_dir])


def main():
    """Main entry point"""
    print("\n" + "="*70)
    print("CROP FIX - Batch Re-crop with Fixed Dimensions")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70 + "\n")

    # Ensure temp directory exists
    os.makedirs(TEMP_DIR, exist_ok=True)

    source_dir = os.path.join(BASE_DIR, "post_noncropped")
    output_dir = os.path.join(BASE_DIR, "post")

    # Find reference file (case-insensitive)
    reference_path = find_file_case_insensitive(source_dir, REFERENCE_FILE)

    if not reference_path:
        print(f"ERROR: Reference file not found: {REFERENCE_FILE}")
        return

    print(f"Found reference file: {reference_path}")

    # Step 1: Get crop dimensions from reference
    crop_params = get_reference_dimensions(reference_path)

    if not crop_params:
        print("ERROR: Failed to get crop parameters from reference")
        return

    # Step 2: Process each target file
    print(f"\nProcessing {len(TARGET_FILES)} target files...")

    success_count = 0
    fail_count = 0

    for filename in TARGET_FILES:
        # Find input file (case-insensitive)
        input_file = find_file_case_insensitive(source_dir, filename)

        if not input_file:
            print(f"WARNING: File not found: {filename}")
            fail_count += 1
            continue

        # Output file always lowercase .mp4
        output_filename = os.path.splitext(filename)[0] + ".mp4"
        output_file = os.path.join(output_dir, output_filename)

        # Delete existing h264 file if present
        h264_output = os.path.splitext(output_file)[0] + "_h264.mp4"
        if os.path.exists(h264_output):
            os.remove(h264_output)
            print(f"Removed existing: {h264_output}")

        # Process
        if process_with_fixed_dimensions(input_file, output_file, crop_params):
            success_count += 1
        else:
            fail_count += 1

    # Summary
    print("\n" + "="*70)
    print("COMPLETE")
    print(f"  Success: {success_count}/{len(TARGET_FILES)}")
    print(f"  Failed:  {fail_count}/{len(TARGET_FILES)}")
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
