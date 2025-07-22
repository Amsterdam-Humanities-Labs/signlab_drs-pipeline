import cv2
import os
import uuid
import mediapipe as mp
import math
import tempfile
import numpy as np
import shutil
import subprocess
import sys
# Assuming other necessary imports like shutil, subprocess for video processing functions exist

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
        "ffmpeg", "-y", "-i", input_file, 
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

def cleanup_temp_dirs(dirs):
    """Clean up temporary directories with frame images"""
    for dir_path in dirs:
        if dir_path and os.path.exists(dir_path):
            try:
                shutil.rmtree(dir_path)
                print(f"Cleaned up temporary directory: {dir_path}")
            except Exception as e:
                print(f"Error cleaning up temporary directory {dir_path}: {e}")

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
    if frame is None:
        print(f"Error: Could not read image {frame_path}")
        return None, None, None, None, None, None, None, None, None, None, None

    with mp_pose.Pose(static_image_mode=True, min_detection_confidence=0.5) as pose:
        img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = pose.process(img_rgb)
        if not results.pose_landmarks:
            print(f"Warning: No pose landmarks detected in {frame_path}")
            # Return default values or handle as needed, e.g., use full frame height
            return 0, frame.shape[0], frame.shape, frame.shape[1] // 2, None, None, None, None, None, None, None

        lm = results.pose_landmarks.landmark
        img_height, img_width = frame.shape[:2]

        # Use nose and shoulders/hips for vertical bounds if available
        try:
            head_top_y = lm[mp_pose.PoseLandmark.NOSE].y * img_height
            # Add some margin above the nose
            head_top = int(head_top_y - 500) # Adjust margin as needed

            left_hip_y = lm[mp_pose.PoseLandmark.LEFT_HIP].y * img_height
            right_hip_y = lm[mp_pose.PoseLandmark.RIGHT_HIP].y * img_height
            waist_y = int((left_hip_y + right_hip_y) / 2)

            # Calculate horizontal center based on hips
            left_hip_x = lm[mp_pose.PoseLandmark.LEFT_HIP].x * img_width
            right_hip_x = lm[mp_pose.PoseLandmark.RIGHT_HIP].x * img_width
            midpoint_x = int((left_hip_x + right_hip_x) / 2)

            # Get nose coordinates for rotation calculation
            nose_x = lm[mp_pose.PoseLandmark.NOSE].x * img_width
            nose_y = lm[mp_pose.PoseLandmark.NOSE].y * img_height

            # Ensure head_top is not negative
            head_top = max(0, head_top)
            # Ensure waist_y is within bounds
            waist_y = min(img_height, waist_y)

            # Return relevant coordinates
            # Returning None for unused shoulder/head_x coordinates for simplicity
            return head_top, waist_y, frame.shape, midpoint_x, None, None, None, None, None, nose_x, nose_y

        except IndexError:
             print(f"Warning: Could not access all required landmarks in {frame_path}")
             # Fallback if specific landmarks are missing
             return 0, img_height, frame.shape, img_width // 2, None, None, None, None, None, None, None

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
                crop_width = 1920
                half_crop = crop_width // 2
                current_width = processed_image.shape[1]
                
                # Calculate crop boundaries
                new_left = max(0, midpoint_x - half_crop)
                new_right = min(current_width, new_left + crop_width)
                
                # Adjust if we hit the right edge
                if new_right == current_width:
                    new_left = max(0, current_width - crop_width)
                
                #target display aspect ratio is 16:9 (1920x1080)
                #target height is 1080
                target_width = 1920

                #we get target_height from processed_image and set 16:9 aspect ratio on target_width
                target_height = int(target_width * target_height / processed_image.shape[1])
                #crop the image
                processed_image = processed_image[head:waist, new_left:new_right]
                # Resize the cropped image to 1920x1080
                processed_image = cv2.resize(processed_image, (target_width, target_height))
                
        


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
    h, w = first_frame.shape[:2]  # expected dimensions: 1920 * 1080
    
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')  # Using MPEG-4 codec
    out = cv2.VideoWriter(output_path, fourcc, fps, (w, h))
    
    for frame_path in frame_paths:
        frame = cv2.imread(frame_path)
        out.write(frame)
        # Free memory
        del frame
    
    out.release()
    return True

def process_video_file(input_file, output_file, temp_dir=None):
    global GLOBAL_HEAD, GLOBAL_WAIST, GLOBAL_MIDPOINT_X, GLOBAL_NOSE_X, GLOBAL_NOSE_Y, GLOBAL_ROTATION_ANGLE, GLOBAL_IMG_HEIGHT, GLOBAL_IMG_WIDTH
    
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
            return
        
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
        
        processed_paths = []
        
        # Process all frames using the same detection parameters from first frame
        for idx, frame_path in enumerate(frame_paths):
            output_path = os.path.join(processed_dir, f"processed_{idx:06d}.jpg")
            is_first_frame = (idx == 0)
            
            # Process the frame (only first frame does detection)
            process_single_image(frame_path, output_path, is_first_frame)
            processed_paths.append(output_path)
        
        # Bundle processed frames into a video
        if not write_video(processed_paths, output_file, fps):
            print(f"Failed to write video from processed frames for {input_file}")
            cleanup_temp_dirs([temp_dir])
            return
        
        # Re-encode with ffmpeg using h264
        reencoded_file = reencode_with_ffmpeg(output_file)

        
        
            
    except Exception as e:
        print(f"Error processing {input_file}: {str(e)}")
        import traceback
        traceback.print_exc()
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
        return int(head_y) - 200

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

def process_single_image(image_path, output_path, is_first_frame=True, debug=False):
    """
    Processes a single image: detects pose, calculates rotation, crops vertically and horizontally,
    resizes while maintaining aspect ratio, and centers the result on a 1920x1080 canvas.
    
    When is_first_frame is True, it performs pose detection.
    Otherwise, it reuses the global variables set by the first frame.
    """
    global GLOBAL_HEAD, GLOBAL_WAIST, GLOBAL_MIDPOINT_X, GLOBAL_NOSE_X, GLOBAL_NOSE_Y, GLOBAL_ROTATION_ANGLE, GLOBAL_IMG_HEIGHT, GLOBAL_IMG_WIDTH, GLOBAL_HEAD_NEW, GLOBAL_WAIST_NEW, GLOBAL_NOSE_NEW
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
        head_new = GLOBAL_HEAD_NEW
        waist_new = GLOBAL_WAIST_NEW
        nose_new = GLOBAL_NOSE_NEW
        
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
    
    #print image dimensions after rotation
    # print("Image dimensions after rotation:", processed_image.shape[0], processed_image.shape[1])

    # Update coordinates after rotation
    head_pt = np.array([midpoint_x, head, 1])
    waist_pt = np.array([midpoint_x, waist, 1])
    rotated_head_pt = rotation_matrix @ head_pt
    rotated_waist_pt = rotation_matrix @ waist_pt
    
    # Get rotated coordinates
    head = int(rotated_head_pt[1])
    waist = int(rotated_waist_pt[1])
    midpoint_x = int(rotated_head_pt[0])
    
    # Ensure coordinates are within bounds
    head = max(0, min(img_height-1, head))
    waist = max(head+1, min(img_height, waist))
    midpoint_x = max(0, min(img_width-1, midpoint_x))

    # Calculate crop width centered around midpoint
    crop_width = 3840
    half_width = crop_width // 2
    left = max(0, midpoint_x - half_width)
    right = min(img_width, left + crop_width)
    
    # Adjust left boundary if needed
    if right == img_width:
        left = max(0, img_width - crop_width)
    
    # Vertical crop
    if head < waist:
        processed_image = processed_image[head:waist, left:right]
    else:
        print(f"Warning: Invalid vertical crop dimensions. Using default crop.")
        processed_image = processed_image[:, left:right]
    
    # Enlarge by 10%
    processed_image = cv2.resize(processed_image, None, fx=1.1, fy=1.1, interpolation=cv2.INTER_LINEAR)
    
    # Resize to target height while maintaining aspect ratio
    target_height = 1080
    target_width = int(target_height * processed_image.shape[1] / processed_image.shape[0])
    processed_image = cv2.resize(processed_image, (target_width, target_height), interpolation=cv2.INTER_LINEAR)
    
    # Center crop to 1920x1080 if wider
    if processed_image.shape[1] > 1920:
        excess_width = processed_image.shape[1] - 1920
        left_crop = excess_width // 2
        right_crop = excess_width - left_crop
        processed_image = processed_image[:, left_crop:processed_image.shape[1]-right_crop]

    if is_first_frame:
        #get bbox function to get head from processed image
        head_new = get_head_coords(processed_image)
        waist_new = get_waist_coords(processed_image)
        nose_new = get_nose_coords(processed_image)
        # Store new head position for future frames
        GLOBAL_HEAD_NEW = head_new
        GLOBAL_WAIST_NEW = waist_new
        GLOBAL_NOSE_NEW = nose_new
        # print("Head position in new image:", head_new)

        # #print the head_new cv2 coordinates circle on head in red color
        # cv2.circle(processed_image, (960, head_new), 10, (0, 0, 255), -1)
        # print("Head position in new image:", head_new)
        # print("image height", processed_image.shape[0])
        # #save the processed image with head_new, waist_new and midpoint_x
        # cv2.imwrite("debug_head.jpg", processed_image)
        # print("Debug image saved at debug_head.jpg")
    #check if head is more than 50 pixels from top, otherwise scale image based on difference
    
    #crop the processed_image to head_new and waist_new
    processed_image = processed_image[head_new:waist_new, :]


    new_img_width = processed_image.shape[1]

    #calculate ideal width for the height based on 16:9 dimension
    ideal_width = int(1920 * processed_image.shape[0] / 1080)
    print("Ideal width:", ideal_width)

    ##based on midpoint of frame from nose_new, crop the left and right based on ideal width / 2
    left = nose_new - ideal_width // 2
    right = nose_new + ideal_width // 2
    # Ensure left and right are within bounds
    left = max(0, left)
    right = min(new_img_width, right)


    print("Nose coords", nose_new)

    processed_image = processed_image[:, left:right]
    # Resize to 1920x1080
    processed_image = cv2.resize(processed_image, (1920, 1080), interpolation=cv2.INTER_LINEAR)

    # final_width = processed_image.shape[1] / 2

    #draw a line middle on the processed_image
    cv2.line(processed_image, (960, 0), (960, 1080), (255, 255, 255), 5)
    



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

if __name__ == "__main__":
    # Define input and output paths for single image processing
    input_image = "first_frame001.jpg"  # Make sure this image exists in the same directory or provide full path
    output_image = "processed_image.jpg"

    # # # Check if input image exists
    # if os.path.exists(input_image):
    #     process_single_image(input_image, output_image, is_first_frame=True, debug=True)
    # else:
    #     print(f"Error: Input image not found at {input_image}")

    # Example of how video processing could still be called (optional)
    input_video = "M20250331_6578.mp4"
    output_video = input_video.replace(".mp4", "_processed.mp4")
    if os.path.exists(input_video):
        process_video_file(input_video, output_video)
    else:
        print(f"Error: Input video not found at {input_video}")