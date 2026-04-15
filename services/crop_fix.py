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
import glob
sys.path.insert(0, '/Users/signlab/drs/shared')
from signcollect_monitor import SignCollectMonitor  # Import the monitor client

# Ensure output is flushed immediately to logs
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Initialize the monitor
crop_fix_monitor = SignCollectMonitor(
    client_id='drs-crop-fix',
    client_name='DRS Crop Fix',
    description='Video re-cropping service for fix requests',
    heartbeat_interval=900
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


def get_hand_landmarks(frame_path):
    """
    Detect hand landmarks using MediaPipe Hands.
    Returns list of all finger landmarks (both hands) with their pixel coordinates.
    """
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
                    # Convert normalized coordinates to pixel coordinates
                    x_px = landmark.x * frame.shape[1]
                    y_px = landmark.y * frame.shape[0]
                    all_hand_landmarks.append((x_px, y_px))

    return all_hand_landmarks, frame.shape


def calculate_boundary_expansion(initial_bounds, hand_landmarks, frame_shape, margin=50):
    """
    Calculate crop boundaries to include all fingers with guaranteed margin.

    Args:
        initial_bounds: dict with keys 'top', 'bottom', 'left', 'right' (in pixels)
        hand_landmarks: list of (x, y) tuples for all detected hand landmarks
        frame_shape: (height, width, channels) of the frame
        margin: minimum distance in pixels between any keypoint and the border

    Returns:
        expanded_bounds: dict with adjusted 'top', 'bottom', 'left', 'right' values
    """
    if not hand_landmarks:
        # No hands detected, return original bounds
        return initial_bounds

    # Find the extreme positions of all hand landmarks
    x_positions = [x for x, y in hand_landmarks]
    y_positions = [y for x, y in hand_landmarks]

    min_x = min(x_positions)
    max_x = max(x_positions)
    min_y = min(y_positions)
    max_y = max(y_positions)

    # Calculate bounds with margin from extreme landmark positions
    # Ensure margin on all sides from the outermost keypoints
    hand_left = max(0, min_x - margin)
    hand_right = min(frame_shape[1], max_x + margin)
    hand_top = max(0, min_y - margin)
    hand_bottom = min(frame_shape[0], max_y + margin)

    # Combine with initial bounds (take the larger crop area to include both body and hands)
    expanded = {
        'top': min(initial_bounds['top'], hand_top),
        'bottom': max(initial_bounds['bottom'], hand_bottom),
        'left': min(initial_bounds['left'], hand_left),
        'right': max(initial_bounds['right'], hand_right)
    }

    return expanded


def save_debug_bbox_frame(frame_path, head, waist, margin, output_path, midpoint_x=None, hand_landmarks=None, expanded_bounds=None):
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

    # Draw hand landmarks if available
    if hand_landmarks is not None and len(hand_landmarks) > 0:
        for x, y in hand_landmarks:
            # Draw small circles for each finger landmark
            cv2.circle(debug_frame, (int(x), int(y)), 3, (255, 0, 255), -1)  # Magenta circles

    # Draw expanded bounds if available (in cyan/blue color to distinguish from original)
    if expanded_bounds is not None:
        top = int(expanded_bounds['top'])
        bottom = int(expanded_bounds['bottom'])
        left = int(expanded_bounds['left'])
        right = int(expanded_bounds['right'])

        # Draw expanded boundary rectangle in cyan
        cv2.rectangle(debug_frame, (left, top), (right, bottom), (255, 255, 0), 3)

        # Add text for expanded bounds
        cv2.putText(debug_frame, "Expanded bounds (cyan)",
                    (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)

    # Draw overlaid text with information
    cv2.putText(debug_frame, f"Head: {head}, Waist: {waist}, Margin: {margin}",
                (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    if midpoint_x is not None:
        cv2.putText(debug_frame, f"Midpoint X: {midpoint_x}",
                    (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    if hand_landmarks is not None:
        cv2.putText(debug_frame, f"Hand landmarks detected: {len(hand_landmarks)}",
                    (20, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 255), 2)

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

def process_frames(frame_paths, output_dir=None, oob=None):
    """
    Process frames saved on disk with hand detection and automatic boundary expansion.
    Skips first 30 and last 60 frames to avoid resting poses.

    oob: dict with keys top/left/right/bottom (bool). Only expand on sides marked True.
    """
    if oob is None:
        oob = {"top": True, "left": True, "right": True, "bottom": False}
    if output_dir is None:
        output_dir = os.path.join(tempfile.gettempdir(), f"processed_{uuid.uuid4().hex}")
        os.makedirs(output_dir, exist_ok=True)

    # Process the first frame to get vertical bounds and midpoint
    first_frame_path = frame_paths[0]
    head, waist, frame_shape, midpoint_x, left_shoulder_x, right_shoulder_x, left_shoulder_y, right_shoulder_y, head_x, nose_x, nose_y = get_vertical_bbox(first_frame_path)

    # Check if pose was detected
    if midpoint_x is None:
        raise ValueError("No pose landmarks detected in video - cannot process")

    # Detect hand landmarks, skipping first and last 30 frames to avoid resting pose
    skip_frames = 30
    total_frames = len(frame_paths)

    if total_frames > (skip_frames * 2 + 10):
        frames_to_analyze = frame_paths[skip_frames:-skip_frames]
        frame_offset = skip_frames
        print(f"Detecting hand landmarks on frames {skip_frames} to {total_frames - skip_frames} (skipping resting pose)...")
    else:
        frames_to_analyze = frame_paths
        frame_offset = 0
        print(f"Video too short ({total_frames} frames), analyzing all frames for hand landmarks...")

    all_hand_landmarks = []
    frame_landmark_log = []  # For JSON logging

    # Sample every Nth frame for efficiency (set to 1 to check every frame)
    sample_rate = 1  # Check every frame for maximum accuracy

    for idx, frame_path in enumerate(frames_to_analyze[::sample_rate]):
        hand_landmarks, _ = get_hand_landmarks(frame_path)

        # Log this frame's landmarks
        frame_log = {
            'frame_number': frame_offset + (idx * sample_rate),
            'landmarks': [{'x': float(x), 'y': float(y)} for x, y in hand_landmarks],
            'landmark_count': len(hand_landmarks)
        }
        frame_landmark_log.append(frame_log)

        # Accumulate all landmarks
        all_hand_landmarks.extend(hand_landmarks)

        if idx % 30 == 0:  # Progress update every 30 sampled frames
            print(f"  Processed {frame_offset + (idx * sample_rate)}/{total_frames} frames, total landmarks: {len(all_hand_landmarks)}")

    print(f"Detected total of {len(all_hand_landmarks)} hand landmarks across {len(frames_to_analyze)} analyzed frames")

    processed_paths = []

    first_frame = cv2.imread(first_frame_path)
    first_framee = first_frame.copy()

    # Calculate angle for when videocamera is tilted
    midpoint_y = first_framee.shape[0]

    # Compute differences
    dx = nose_x - midpoint_x
    vertical_distance = midpoint_y - nose_y

    # Calculate the angle in radians
    angle_rad = math.atan2(dx, vertical_distance)
    angle_deg = math.degrees(angle_rad)
    rotation_angle = angle_deg

    print(f"Rotation angle: {rotation_angle:.2f} degrees")

    # Apply rotation transformation to hand landmarks BEFORE calculating boundaries
    rotated_hand_landmarks = []
    rotation_matrix = cv2.getRotationMatrix2D((midpoint_x, midpoint_y), rotation_angle, 1.0)

    for x, y in all_hand_landmarks:
        # Apply rotation matrix to each landmark point
        point = np.array([[[x, y]]], dtype=np.float32)
        rotated_point = cv2.transform(point, rotation_matrix)
        rotated_x = rotated_point[0][0][0]
        rotated_y = rotated_point[0][0][1]
        rotated_hand_landmarks.append((rotated_x, rotated_y))

    print(f"Applied rotation to {len(rotated_hand_landmarks)} hand landmarks")

    # Calculate initial crop boundaries based on BODY (shoulders), not hardcoded 1440
    # This allows hand landmarks to genuinely expand the crop beyond the body,
    # so the final resize to 1440x1252 zooms out the person to fit hands.
    current_width = first_framee.shape[1]
    shoulder_margin = 100  # Small margin around shoulders for the body-only bounds
    body_left = max(0, int(min(left_shoulder_x, right_shoulder_x)) - shoulder_margin)
    body_right = min(current_width, int(max(left_shoulder_x, right_shoulder_x)) + shoulder_margin)

    # Create initial bounds dictionary based on body
    initial_bounds = {
        'top': head,
        'bottom': waist,
        'left': body_left,
        'right': body_right
    }

    print(f"Initial crop bounds (body) - Top: {head}, Bottom: {waist}, Left: {body_left}, Right: {body_right}")

    # Calculate expanded bounds using ROTATED hand landmarks
    expanded_bounds = calculate_boundary_expansion(
        initial_bounds,
        rotated_hand_landmarks,
        first_framee.shape,
        margin=50  # Guarantees 50px margin from outermost keypoints to border
    )

    print(f"Expanded crop bounds - Top: {expanded_bounds['top']}, Bottom: {expanded_bounds['bottom']}, "
          f"Left: {expanded_bounds['left']}, Right: {expanded_bounds['right']}")

    # Only expand on sides flagged in oob; keep initial bounds for unflagged sides
    if not oob.get('top', True):
        expanded_bounds['top'] = initial_bounds['top']
    if not oob.get('left', True):
        expanded_bounds['left'] = initial_bounds['left']
    if not oob.get('right', True):
        expanded_bounds['right'] = initial_bounds['right']
    if not oob.get('bottom', False):
        expanded_bounds['bottom'] = initial_bounds['bottom']  # = waist

    print(f"OOB-filtered bounds - Top: {expanded_bounds['top']}, Bottom: {expanded_bounds['bottom']}, "
          f"Left: {expanded_bounds['left']}, Right: {expanded_bounds['right']} (oob: {oob})")

    # Enforce 1:1.15 aspect ratio (height:width = 1:1.15)
    frame_width = first_framee.shape[1]
    final_bottom = int(expanded_bounds['bottom'])
    final_top = max(0, int(expanded_bounds['top']))

    # Track hand bounds needed (for centering and inclusion)
    hand_left = max(0, int(expanded_bounds['left']))
    hand_right = min(frame_width, int(expanded_bounds['right']))
    hand_width_needed = hand_right - hand_left

    final_height = final_bottom - final_top
    required_width = int(final_height * 1.15)

    # If hands need more width than ratio provides, increase height to maintain ratio
    if hand_width_needed > required_width:
        required_width = hand_width_needed
        final_height = int(required_width / 1.15)
        final_top = max(0, final_bottom - final_height)
        # Recalculate after potential top clamping
        final_height = final_bottom - final_top
        required_width = int(final_height * 1.15)

    # Clamp to frame width if needed, adjusting height to maintain ratio
    if required_width > frame_width:
        required_width = frame_width
        final_height = int(required_width / 1.15)
        final_top = max(0, final_bottom - final_height)

    # Center width around midpoint_x, then shift to include all hand landmarks
    final_left = midpoint_x - required_width // 2
    final_right = final_left + required_width

    # Shift to ensure hand landmarks stay within bounds
    if final_left > hand_left:
        final_left = hand_left
        final_right = final_left + required_width
    if final_right < hand_right:
        final_right = hand_right
        final_left = final_right - required_width

    # Calculate padding needed to keep person centered (instead of clamping to frame edges)
    pad_left = max(0, -final_left)
    pad_right = max(0, final_right - frame_width)
    pad_top = max(0, -final_top)

    # Crop coordinates clamped to valid frame region (padding fills the rest)
    crop_left = max(0, final_left)
    crop_right = min(frame_width, final_right)
    crop_top = max(0, final_top)
    crop_bottom = final_bottom

    if pad_left > 0 or pad_right > 0 or pad_top > 0:
        print(f"Padding needed - left: {pad_left}px, right: {pad_right}px, top: {pad_top}px")

    print(f"Enforced 1:1.15 ratio - Final: {required_width}x{final_bottom - final_top} "
          f"(top={final_top}, bottom={final_bottom}, left={final_left}, right={final_right})")

    # Convert final bounds to regular Python floats for JSON serialization
    expanded_bounds_serializable = {
        'top': float(final_top),
        'bottom': float(final_bottom),
        'left': float(final_left),
        'right': float(final_right)
    }

    # Create comprehensive log with boundary information
    boundary_log = {
        'video_info': {
            'total_frames': len(frame_paths),
            'sampled_frames': len(frame_landmark_log),
            'sample_rate': sample_rate,
            'frame_shape': {'height': first_framee.shape[0], 'width': first_framee.shape[1]}
        },
        'initial_bounds': initial_bounds,
        'expanded_bounds': expanded_bounds_serializable,
        'expansion_applied': {
            'top': initial_bounds['top'] - expanded_bounds_serializable['top'],
            'bottom': expanded_bounds_serializable['bottom'] - initial_bounds['bottom'],
            'left': initial_bounds['left'] - expanded_bounds_serializable['left'],
            'right': expanded_bounds_serializable['right'] - initial_bounds['right']
        },
        'frames': frame_landmark_log
    }

    # Add out-of-bounds analysis to each frame
    margin_check = 50  # Required margin
    for frame_data in boundary_log['frames']:
        out_of_bounds = []
        margin_violations = []

        for i, landmark in enumerate(frame_data['landmarks']):
            # Apply rotation to this landmark
            point = np.array([[[landmark['x'], landmark['y']]]], dtype=np.float32)
            rotated_point = cv2.transform(point, rotation_matrix)
            rotated_x = float(rotated_point[0][0][0])
            rotated_y = float(rotated_point[0][0][1])

            # Check if ROTATED position is outside bounds
            violations = []
            if rotated_x < final_left:
                violations.append(f"left (rotated_x={rotated_x:.1f} < {final_left})")
            if rotated_x > final_right:
                violations.append(f"right (rotated_x={rotated_x:.1f} > {final_right})")
            if rotated_y < final_top:
                violations.append(f"top (rotated_y={rotated_y:.1f} < {final_top})")
            if rotated_y > final_bottom:
                violations.append(f"bottom (rotated_y={rotated_y:.1f} > {final_bottom})")

            if violations:
                out_of_bounds.append({
                    'landmark_index': i,
                    'position': landmark,
                    'rotated_position': {'x': rotated_x, 'y': rotated_y},
                    'violations': violations
                })

            # Check if ROTATED position has insufficient margin
            margin_issues = []
            dist_left = rotated_x - final_left
            dist_right = final_right - rotated_x
            dist_top = rotated_y - final_top
            dist_bottom = final_bottom - rotated_y

            if dist_left < margin_check:
                margin_issues.append(f"left margin={dist_left:.1f}px (need {margin_check}px)")
            if dist_right < margin_check:
                margin_issues.append(f"right margin={dist_right:.1f}px (need {margin_check}px)")
            if dist_top < margin_check:
                margin_issues.append(f"top margin={dist_top:.1f}px (need {margin_check}px)")
            if dist_bottom < margin_check:
                margin_issues.append(f"bottom margin={dist_bottom:.1f}px (need {margin_check}px)")

            if margin_issues:
                margin_violations.append({
                    'landmark_index': i,
                    'rotated_position': {'x': rotated_x, 'y': rotated_y},
                    'margin_issues': margin_issues
                })

        frame_data['out_of_bounds_count'] = len(out_of_bounds)
        frame_data['out_of_bounds_details'] = out_of_bounds
        frame_data['margin_violation_count'] = len(margin_violations)
        frame_data['margin_violations'] = margin_violations

    # Save JSON log
    json_log_path = os.path.join(output_dir, "hand_landmarks_log.json")
    with open(json_log_path, 'w') as f:
        json.dump(boundary_log, f, indent=2)
    print(f"Hand landmarks log saved to: {json_log_path}")

    # Create debug image showing detected bounding boxes and hand landmarks from first frame
    first_frame_hands, _ = get_hand_landmarks(first_frame_path)
    debug_bbox_path = os.path.join(output_dir, "debug_bbox.jpg")
    save_debug_bbox_frame(first_frame_path, head, waist, 50, debug_bbox_path, midpoint_x, first_frame_hands, expanded_bounds)

    for idx, frame_path in enumerate(frame_paths):
        frame = cv2.imread(frame_path)
        processed_image = frame.copy()

        rotation_matrix = cv2.getRotationMatrix2D((midpoint_x, midpoint_y), rotation_angle, 1.0)
        processed_image = cv2.warpAffine(processed_image, rotation_matrix, (processed_image.shape[1], processed_image.shape[0]))

        # Crop to valid frame region
        processed_image = processed_image[crop_top:crop_bottom, crop_left:crop_right]

        # Pad to keep person centered when near frame edges (replicate edge pixels for seamless background)
        if pad_top > 0 or pad_left > 0 or pad_right > 0:
            processed_image = cv2.copyMakeBorder(
                processed_image,
                top=pad_top, bottom=0,
                left=pad_left, right=pad_right,
                borderType=cv2.BORDER_REPLICATE
            )

        # Resize to fixed output resolution: 1440 x 1252 (ratio ~1.15)
        OUTPUT_WIDTH = 1440
        OUTPUT_HEIGHT = 1252
        processed_image = cv2.resize(processed_image, (OUTPUT_WIDTH, OUTPUT_HEIGHT), interpolation=cv2.INTER_LANCZOS4)

        # Save the processed frame
        output_frame_path = os.path.join(output_dir, f"processed_{idx:06d}.jpg")
        cv2.imwrite(output_frame_path, processed_image)
        processed_paths.append(output_frame_path)

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

def process_video_file(input_file, output_file, temp_dir=None, oob=None):
    if oob is None:
        oob = {"top": True, "left": True, "right": True, "bottom": False}
    try:
        print(f"Processing file: {input_file}")
        print(f"OOB sides to fix: {oob}")

        # Create a unique temp directory for this task if not provided
        if temp_dir is None:
            temp_dir = os.path.join(tempfile.gettempdir(), f"task_{uuid.uuid4().hex}")
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

        processed_paths, _, head, waist, margin, _ = process_frames(frame_paths, processed_dir, oob)

        # Write video from processed frames on disk
        write_video(processed_paths, output_file, fps)

        # Re-encode with ffmpeg using h264
        reencoded_file = reencode_with_ffmpeg(output_file)

        # If re-encoding succeeded, upload the video
        if reencoded_file:
            upload_success = upload_video(reencoded_file)
            if upload_success:
                print(f"Video processing and upload complete for {input_file}")
            else:
                print(f"Failed to upload {reencoded_file}")
            print(f"Output: {reencoded_file}")
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

# ----- Fix Mode Functions -----

def fetch_unresolved_fixes():
    """Fetch unresolved video fixes from API.

    Returns a list of fix groups, each containing:
    - m_file: parent identifier for API calls
    - oob: dict with top/left/right/bottom booleans
    - files: list of unresolved file entries [{file, type}, ...]
    """
    api_url = "https://signcollect.nl/videoFix/crop_fixes.json"

    try:
        print(f"Fetching unresolved fixes from {api_url}")
        response = requests.get(api_url, verify=False, timeout=30)
        response.raise_for_status()
        data = response.json()

        fix_groups = []
        for fix in data.get("fixes", []):
            m_file = fix.get("m_file")
            oob = fix.get("oob", {"top": True, "left": True, "right": True, "bottom": False})
            files_array = fix.get("files", [])

            unresolved = [
                {"file": fe.get("file"), "type": fe.get("type")}
                for fe in files_array
                if fe.get("status") == "unresolved" and fe.get("resolved_at") is None
            ]

            if unresolved:
                fix_groups.append({
                    "m_file": m_file,
                    "oob": oob,
                    "files": unresolved,
                })

        total_files = sum(len(g["files"]) for g in fix_groups)
        print(f"Found {len(fix_groups)} fix group(s) ({total_files} files)")
        return fix_groups
    except Exception as e:
        print(f"Failed to fetch fixes: {e}")
        return []

def mark_fix_resolved(m_file, file_name, max_retries=3):
    """Mark a specific file as resolved on the server.

    Args:
        m_file: Parent m_file identifier (used to find the fix entry)
        file_name: Specific filename to mark as resolved (m_file, l_file, or r_file value)
        max_retries: Number of retry attempts for transient failures
    """
    update_url = "https://signcollect.nl/videoFix/api.php?action=update_status"

    payload = {
        "m_file": m_file,
        "file": file_name,
        "status": "resolved"
    }

    for attempt in range(max_retries):
        try:
            response = requests.post(
                update_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                verify=False,
                timeout=30
            )
            response.raise_for_status()

            # Check response body for success
            result = response.json()
            if result.get("success"):
                print(f"✓ Marked {file_name} as resolved")
                return True
            elif "error" in result:
                error_msg = result.get("error", "Unknown error")
                # Retry on transient write failures
                if "Failed to write" in error_msg and attempt < max_retries - 1:
                    print(f"  Retry {attempt + 1}/{max_retries}: {error_msg}")
                    time.sleep(1)
                    continue
                print(f"✗ API error for {file_name}: {error_msg}")
                return False
            else:
                print(f"✗ Unexpected response for {file_name}: {result}")
                return False

        except Exception as e:
            if attempt < max_retries - 1:
                print(f"  Retry {attempt + 1}/{max_retries}: {e}")
                time.sleep(1)
                continue
            print(f"✗ Failed to update {file_name}: {e}")
            return False

    return False

def find_video_in_directories(base_dir, filename_without_ext):
    """Search all date directories for a video file."""
    # Convert .wav to .mp4
    mp4_filename = f"{filename_without_ext}.mp4"

    # Parse date from filename: M20251201_7047 -> 2025-12-01
    # First character is camera letter (M/L/R), next 8 chars are YYYYMMDD
    try:
        date_part = filename_without_ext[1:9]  # Extract YYYYMMDD
        year = date_part[0:4]
        month = date_part[4:6]
        day = date_part[6:8]
        folder_date = f"{year}-{month}-{day}"  # Format as 2025-12-01
        print(f"  Parsed date from filename: {date_part} -> {folder_date}")
    except (IndexError, ValueError) as e:
        print(f"WARNING: Could not parse date from filename {filename_without_ext}: {e}")
        return None, None

    # Construct direct path
    date_dir = os.path.join(str(base_dir), folder_date)
    input_path = os.path.join(date_dir, "post_noncropped", mp4_filename)
    output_path = os.path.join(date_dir, "post", mp4_filename)

    print(f"  Looking for: {input_path}")

    if not os.path.exists(input_path):
        print(f"WARNING: Video not found: {input_path}")
        # Fallback: try glob pattern in case date parsing is wrong
        search_pattern = os.path.join(str(base_dir), "*", "post_noncropped", mp4_filename)
        matches = glob.glob(search_pattern)
        if matches:
            input_path = matches[0]
            date_dir = os.path.dirname(os.path.dirname(input_path))
            output_path = os.path.join(date_dir, "post", mp4_filename)
            print(f"  Found via glob fallback: {input_path}")
        else:
            return None, None

    return input_path, output_path

def cleanup_existing_outputs(output_path):
    """Delete existing output files and error JSONs."""
    if not output_path:
        return

    base_path = os.path.splitext(output_path)[0]
    extension = os.path.splitext(output_path)[1]

    # Derive input directory for error JSON
    output_dir = os.path.dirname(output_path)
    input_dir = output_dir.replace("/post", "/post_noncropped")
    basename = os.path.basename(base_path)
    input_base = os.path.join(input_dir, basename)

    files_to_delete = [
        output_path,                      # M20251201_7047.mp4
        f"{base_path}_h264{extension}",   # M20251201_7047_h264.mp4
        f"{base_path}_error.json",        # error.json in post/
        f"{input_base}_error.json",       # error.json in post_noncropped/
    ]

    for file_path in files_to_delete:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                print(f"  Deleted: {os.path.basename(file_path)}")
            except OSError as e:
                print(f"  WARNING: Could not delete {file_path}: {e}")

def process_fix_tasks(tasks, temp_base):
    """Process fix tasks with parallel execution.

    Args:
        tasks: List of tuples (input_file, output_file, m_file_parent, file_name, oob)
        temp_base: Base directory for temporary files
    """
    if not tasks:
        return

    print(f"\nProcessing {len(tasks)} fix task(s)...")

    with ProcessPoolExecutor(max_workers=5) as executor:
        futures_to_tasks = {}

        for input_file, output_file, m_file_parent, file_name, oob in tasks:
            task_temp_dir = os.path.join(temp_base, f"fix_{uuid.uuid4().hex}")
            os.makedirs(task_temp_dir, exist_ok=True)

            future = executor.submit(
                process_video_file,
                input_file,
                output_file,
                task_temp_dir,
                oob
            )
            futures_to_tasks[future] = (input_file, output_file, m_file_parent, file_name, task_temp_dir)

        for future in as_completed(futures_to_tasks.keys()):
            input_file, output_file, m_file_parent, file_name, temp_dir = futures_to_tasks[future]

            try:
                future.result()

                # Check if h264 output exists
                base_path = os.path.splitext(output_file)[0]
                h264_output = f"{base_path}_h264{os.path.splitext(output_file)[1]}"

                if os.path.exists(h264_output):
                    print(f"✓ SUCCESS: {os.path.basename(input_file)}")
                    mark_fix_resolved(m_file_parent, file_name)
                else:
                    print(f"✗ FAILED: {os.path.basename(input_file)}")

            except Exception as e:
                print(f"✗ FAILED: {os.path.basename(input_file)} - {e}")

            finally:
                if os.path.exists(temp_dir):
                    cleanup_temp_dirs([temp_dir])

def main_fix_mode(loop=False, interval_minutes=60):
    """Process unresolved video fixes from API.
    Processes each fix group (M/L/R) immediately instead of building a full list first.
    """
    # Register with monitoring system
    crop_fix_monitor.register()

    base_dir = Path("/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles")
    temp_base = "/Users/signlab/drs/temp/"

    while True:
        # Send heartbeat at start of each cycle
        crop_fix_monitor.send_heartbeat()

        print("\n" + "="*70)
        print(f"FIX MODE - Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*70 + "\n")

        # Fetch unresolved fix groups
        fix_groups = fetch_unresolved_fixes()

        if not fix_groups:
            print("No unresolved fixes found.")
        else:
            # Process each fix group immediately
            for group_idx, group in enumerate(fix_groups):
                m_file_parent = group["m_file"]
                oob = group["oob"]
                files = group["files"]

                print(f"\n--- Group {group_idx + 1}/{len(fix_groups)}: {m_file_parent} (oob: {oob}) ---")

                # Resolve paths for all files in this group
                tasks = []
                for file_entry in files:
                    file_name = file_entry.get("file")
                    if not file_name:
                        continue

                    filename_without_ext = os.path.splitext(file_name)[0]
                    input_path, output_path = find_video_in_directories(base_dir, filename_without_ext)

                    if input_path is None:
                        print(f"  Not found: {filename_without_ext}.mp4")
                        continue

                    cleanup_existing_outputs(output_path)
                    tasks.append((input_path, output_path, m_file_parent, file_name, oob))

                # Process this group's tasks immediately
                if tasks:
                    process_fix_tasks(tasks, temp_base)
                else:
                    print(f"  No valid files for {m_file_parent}")

        if not loop:
            break

        print(f"\nSleeping for {interval_minutes} minutes...")
        time.sleep(interval_minutes * 60)

# ----- Directory Loop -----

def main():
    tasks = []
    homedir = Path("/Users/signlab/")
    base_dir = Path("/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles")    # Loop through each parent directory in base_dir (e.g., "2025-03-01")
    # base_dir = Path("/Users/gomerotterspeer/drs/landscape")    # Loop through each parent directory in base_dir (e.g., "2025-03-01")
    # homedir = Path("/Users/gomerotterspeer/")

    # Only process folders from the last 2 weeks
    cutoff_date = datetime.now() - timedelta(days=14)
    print(f"Processing folders from {cutoff_date.strftime('%Y-%m-%d')} onwards (last 2 weeks)")

    for subdir in sorted(os.listdir(base_dir), reverse=True):
        date_dir = os.path.join(base_dir, subdir)
        if not os.path.isdir(date_dir):
            continue

        # Parse date from directory name (format: YYYY-MM-DD)
        try:
            dir_date = datetime.strptime(subdir, "%Y-%m-%d")
            if dir_date < cutoff_date:
                continue
        except ValueError:
            # Skip directories that don't match date format
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

            # Check for existing error JSON
            error_json_path = os.path.splitext(input_file)[0] + "_error.json"
            if os.path.exists(error_json_path):
                print(f"Skipping {filename} - error file exists: {error_json_path}")
                continue

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
    import sys

    # Parse command-line arguments
    loop_mode = "--loop" in sys.argv

    # Setup temp directory
    temp_dir = "/Users/signlab/drs/temp/"
    if os.path.exists(temp_dir):
        print("Cleaning up temp directory before starting...")
        cleanup_old_temp_files(temp_dir, max_age_hours=0)
        cleanup_temp_by_size(temp_dir, max_size_gb=10)
    else:
        os.makedirs(temp_dir, exist_ok=True)

    # Run fix mode
    main_fix_mode(loop=loop_mode, interval_minutes=15)
