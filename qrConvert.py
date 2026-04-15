import cv2
import os
import json
import mysql.connector
from datetime import datetime, date
import re
from collections import defaultdict
from PIL import Image
from qreader import QReader  # Import QReader for QR code detection
import numpy as np
from pyzbar import pyzbar as pyzbar_mod

import psutil
import sys
import os

# ─────────────────────────────────────────────────────────────────────────────
# PARTIAL QR DECODE (reconstructs missing top-right finder pattern)
# ─────────────────────────────────────────────────────────────────────────────

FINDER_PATTERN = np.array([
    [1, 1, 1, 1, 1, 1, 1],
    [1, 0, 0, 0, 0, 0, 1],
    [1, 0, 1, 1, 1, 0, 1],
    [1, 0, 1, 1, 1, 0, 1],
    [1, 0, 1, 1, 1, 0, 1],
    [1, 0, 0, 0, 0, 0, 1],
    [1, 1, 1, 1, 1, 1, 1],
], dtype=np.uint8)


def find_square_finders(gray):
    _, binary = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for c in contours:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.05 * peri, True)
        if len(approx) == 4:
            x, y, cw, ch = cv2.boundingRect(approx)
            ar = cw / ch if ch else 0
            if 0.7 < ar < 1.3 and 20 < cw < gray.shape[1] // 2:
                candidates.append((x, y, cw, ch))
    return sorted(candidates, key=lambda s: -(s[2] * s[3]))


def find_tl_bl_finders(gray):
    squares = find_square_finders(gray)
    if not squares:
        return None, None
    tl = min(squares, key=lambda s: s[0] + s[1])
    tl_size = tl[2]
    bl_candidates = [
        s for s in squares
        if s != tl
        and abs(s[2] - tl_size) < tl_size * 0.3
        and abs(s[0] - tl[0]) < tl_size * 2
        and s[1] > tl[1] + tl_size * 3
    ]
    bl = max(bl_candidates, key=lambda s: s[2] * s[3]) if bl_candidates else None
    return tl, bl


def reconstruct_axis_aligned(img, tl_finder, version):
    x0, y0, fw, fh = tl_finder
    module_size = fw / 7.0
    h, w = img.shape[:2]
    n_modules = (version - 1) * 4 + 21
    tr_x0 = int(x0 + (n_modules - 7) * module_size)
    tr_y0 = y0
    tr_right = tr_x0 + int(7 * module_size)
    pad_needed = max(0, tr_right - w) + int(module_size * 5)
    canvas = cv2.copyMakeBorder(img, 0, 0, 0, pad_needed,
                                 cv2.BORDER_CONSTANT, value=(255, 255, 255))
    for row in range(7):
        for col in range(7):
            px0 = tr_x0 + int(col * module_size)
            py0 = tr_y0 + int(row * module_size)
            px1 = tr_x0 + int((col + 1) * module_size)
            py1 = tr_y0 + int((row + 1) * module_size)
            if px1 > w - 2:
                color = (0, 0, 0) if FINDER_PATTERN[row, col] == 1 else (255, 255, 255)
                cv2.rectangle(canvas, (px0, py0), (px1, py1), color, -1)
    return canvas


def reconstruct_rotated(img, tl_finder, bl_finder, verbose=False):
    x_tl, y_tl, fw_tl, fh_tl = tl_finder
    x_bl, y_bl, fw_bl, fh_bl = bl_finder
    h, w = img.shape[:2]
    qr_tl = np.float32([x_tl, y_tl])
    qr_bl = np.float32([x_bl, y_bl + fh_bl])
    left_vec = qr_bl - qr_tl
    lx, ly = left_vec
    right_vec = np.float32([ly, -lx])
    right_unit = right_vec / np.linalg.norm(right_vec)
    down_unit = left_vec / np.linalg.norm(left_vec)
    module_size = fw_tl / 7.0

    for version in range(3, 8):
        n_modules = (version - 1) * 4 + 21
        expected_side = n_modules * module_size
        qr_tr = qr_tl + expected_side * right_unit
        qr_br = qr_bl + expected_side * right_unit

        if verbose:
            print(f"  v{version}: TR at x={qr_tr[0]:.0f}, missing={qr_tr[0] - w:.0f}px")

        pad_r = max(0, int(qr_tr[0] - w) + int(module_size * 6))
        canvas = cv2.copyMakeBorder(img, 0, 0, 0, pad_r,
                                     cv2.BORDER_CONSTANT, value=(255, 255, 255))
        tr_origin = qr_tl + (n_modules - 7) * module_size * right_unit

        for row in range(7):
            for col in range(7):
                p00 = tr_origin + col * module_size * right_unit + row * module_size * down_unit
                p10 = p00 + module_size * right_unit
                p01 = p00 + module_size * down_unit
                p11 = p00 + module_size * right_unit + module_size * down_unit
                pts = np.array([p00, p10, p11, p01], dtype=np.int32)
                if pts[:, 0].max() > w - 2:
                    color = (0, 0, 0) if FINDER_PATTERN[row, col] == 1 else (255, 255, 255)
                    cv2.fillPoly(canvas, [pts], color)

        size = int(n_modules * module_size)
        src_pts = np.float32([qr_tl, qr_tr, qr_br, qr_bl])
        dst_pts = np.float32([[0, 0], [size, 0], [size, size], [0, size]])
        M = cv2.getPerspectiveTransform(src_pts, dst_pts)
        flat = cv2.warpPerspective(canvas, M, (size, size),
                                    flags=cv2.INTER_LANCZOS4,
                                    borderValue=(255, 255, 255))
        flat_gray = cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY)

        for binarized in [
            cv2.threshold(flat_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
            cv2.threshold(flat_gray, 120, 255, cv2.THRESH_BINARY)[1],
            cv2.adaptiveThreshold(flat_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 15, 4),
        ]:
            padded = cv2.copyMakeBorder(binarized, 20, 20, 20, 20,
                                         cv2.BORDER_CONSTANT, value=255)
            result = pyzbar_mod.decode(padded)
            if result:
                return result[0].data.decode("utf-8")
    return None


def decode_partial_qr(image_input, verbose=False):
    if isinstance(image_input, str):
        img = cv2.imread(image_input)
    else:
        img = image_input.copy()
    if img is None:
        return None

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = img.shape[:2]

    # Step 1: direct decode
    result = pyzbar_mod.decode(gray)
    if result:
        return result[0].data.decode("utf-8")

    # Step 2: find finder patterns
    tl_finder, bl_finder = find_tl_bl_finders(gray)
    if tl_finder is None:
        return None

    module_size = tl_finder[2] / 7.0

    # Step 3A: axis-aligned reconstruction
    for version in range(2, 9):
        n_modules = (version - 1) * 4 + 21
        if tl_finder[0] + n_modules * module_size < w + module_size * 0.3:
            continue
        canvas = reconstruct_axis_aligned(img, tl_finder, version)
        canvas_gray = cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY)
        for proc in [
            canvas_gray,
            cv2.adaptiveThreshold(canvas_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 15, 4),
        ]:
            result = pyzbar_mod.decode(proc)
            if result:
                return result[0].data.decode("utf-8")

    # Step 3B: rotated reconstruction
    if bl_finder is not None:
        result = reconstruct_rotated(img, tl_finder, bl_finder, verbose=verbose)
        if result:
            return result

    return None


# ─────────────────────────────────────────────────────────────────────────────

def is_already_running(script_name):
    """
    Check if another instance of the script is already running.

    Parameters:
    - script_name: Name of the script file (e.g., 'qrConvert.py')

    Returns:~
    - True if another instance is running, False otherwise.
    """
    current_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            # Check if process name matches and it's not the current process
            if 'python' in proc.info['name'] or 'python3' in proc.info['name']:
                cmdline = proc.info['cmdline']
                print(cmdline)
                print(proc.info['pid'])
                print(current_pid)
                if cmdline is not None and len(cmdline) > 1:
                    if "qrConvert" in cmdline[1] and proc.info['pid'] != current_pid:
                        return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False
def preprocess_image(frame):
    """
    Preprocess the image for better QR code detection.
    Steps:
    - Convert the frame to RGB format.
    - (Optional) Apply binarization using kraken's nlbin for enhanced detection.
    """
    # Convert OpenCV's BGR to RGB
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    
    # For debugging: save the preprocessed image with a timestamp
    ms = str(int(datetime.now().timestamp() * 1000))
    Image.fromarray(frame_rgb).save(f"preprocessed_frame_{ms}.png")
    
    # If not using binarization, return the RGB frame
    return frame_rgb

def fill_empty_jsons_from_other_cameras(directory_date_path, date_str):
    """
    For any camera that has empty [] JSON files, try to copy QR data from another camera
    that successfully decoded the QR code at the same index position.
    Only applies when all cameras have the same number of MP4 files.
    """
    # Group files by camera letter
    camera_files = defaultdict(list)
    for filename in os.listdir(directory_date_path):
        if filename.upper().endswith('.MP4') and date_str in filename:
            camera_letter = filename[0].upper()
            camera_files[camera_letter].append(filename)

    if len(camera_files) < 2:
        print("Not enough cameras to cross-reference QR data.")
        return

    # Check if all cameras have the same file count
    counts = [len(files) for files in camera_files.values()]
    if len(set(counts)) != 1:
        print(f"Camera file counts differ ({dict((k, len(v)) for k, v in camera_files.items())}), skipping cross-camera QR fill.")
        return

    # Sort each camera's files by increment number
    for cam in camera_files:
        camera_files[cam].sort(key=lambda f: int(os.path.splitext(f)[0].split('_')[1]))

    file_count = counts[0]
    filled = 0

    for idx in range(file_count):
        for cam in camera_files:
            json_filename = os.path.splitext(camera_files[cam][idx])[0] + '.json'
            json_path = os.path.join(directory_date_path, json_filename)

            # Check if this JSON is empty
            if not os.path.exists(json_path):
                continue
            with open(json_path, 'r') as f:
                data = json.load(f)
            if data:
                continue

            # This JSON is empty, try to find valid data from another camera at same index
            for other_cam in camera_files:
                if other_cam == cam:
                    continue
                other_json = os.path.splitext(camera_files[other_cam][idx])[0] + '.json'
                other_json_path = os.path.join(directory_date_path, other_json)
                if not os.path.exists(other_json_path):
                    continue
                with open(other_json_path, 'r') as f:
                    other_data = json.load(f)
                if other_data:
                    with open(json_path, 'w') as f:
                        json.dump(other_data, f)
                    print(f"Filled {json_filename} with QR data from {other_json} (camera {other_cam})")
                    filled += 1
                    break

    print(f"Cross-camera QR fill complete: filled {filled} empty JSON files.")


def process_videos_in_directory(directory_date_path, qreader):
    """
    Process all MP4 videos in the specified directory to detect and decode QR codes.

    Parameters:
    - directory_path: Path to the directory containing video files.
    - qreader: An instance of QReader for QR code detection.

    Returns:
    - qr_codes_output: List of tuples containing video filenames and decoded QR data.
    """
    qr_codes_output = []
    
    #get date from directory_path just before /raw/
    #so the directory_path is /web/gebarenoverleg_media/studioFiles/2024-10-15/raw/
    #the output of this line should be 2024-10-15
    date_str = directory_date_path.split('/raw/')[0].split('/')[-1]
    date_str = date_str.replace('-', '')
    print("DATUM: "+date_str)
    
    #directory path will be located in studiFilesMini because of faster processing time
    directory_path = "/web/gebarenoverleg_media/studioFilesMini/raw"

    # Iterate over all files in the directory
    for filename in os.listdir(directory_path):
        # print("Processing: ", filename)
        if filename.upper().endswith('.MP4'):
            #check if the filename contains the date same as date_str
            if date_str not in filename:
                print("this file doesnt belong here, because: ", filename, date_str)
                continue
            #video comes from  raw dir consisting of compressed video files
            video_path = os.path.join(directory_path, filename)
            json_filename = os.path.splitext(filename)[0] + '.json'
            
            #directory_date_path will be original dir from rclone
            json_path = os.path.join(directory_date_path, json_filename)

            # **Skip Processing if JSON Already Exists**
            if os.path.exists(json_path):
                print(f"JSON file already exists for {filename}. Skipping processing.")
                continue

            print(f"Processing: {filename}")

            # Open video file
            cap = cv2.VideoCapture(video_path)

            # **Initialize frame_count to 50 to start processing from frame 50**
            frame_count = 50
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_count)

            qr_codes_found = []  # To store decoded QR codes

            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break

                # Preprocess the frame
                preprocessed_frame = preprocess_image(frame)

                # Detect QR codes with bounding boxes
                try:
                    detections = qreader.detect(image=preprocessed_frame)
                except Exception as e:
                    print(f"QReader detect encountered an error in {filename} at frame {frame_count}: {e}")
                    detections = []

                print(f"Detections at frame {frame_count}: {detections}")

                for idx, detection in enumerate(detections):
                    x1, y1, x2, y2 = detection['bbox_xyxy']
                    confidence = detection['confidence']
                    print(f"Detection {idx+1}: BBox=({x1}, {y1}, {x2}, {y2}), Confidence={confidence}")
                    
                    x1 -= 10
                    x2 += 10
                    y1 -= 10
                    y2 += 10

                    # Crop the image based on bounding box
                    cropped_image = preprocessed_frame[int(y1):int(y2), int(x1):int(x2)]

                    # **Process Cropped Image in Memory Without Saving to Disk**
                    try:
                        # Convert cropped image to RGB if not already
                        if cropped_image.ndim == 2:
                            # Grayscale to RGB
                            cropped_image_rgb = cv2.cvtColor(cropped_image, cv2.COLOR_GRAY2RGB)
                        else:
                            cropped_image_rgb = cropped_image

                        # Use the detect_and_decode function to get the decoded QR data
                        decoded_text = qreader.detect_and_decode(image=cropped_image_rgb)
                    except Exception as e:
                        print(f"QReader detect_and_decode encountered an error in {filename} at frame {frame_count}: {e}")
                        decoded_text = None

                    print(f"Decoded text for detection {idx+1} in {filename}: {decoded_text}")

                    # Check if decoded_text contains valid data
                    if decoded_text and decoded_text[0] is not None:
                        try:
                            # Parse the decoded JSON string
                            decoded_data = json.loads(decoded_text[0])
                            qr_codes_found.append(decoded_data)  # Append as list
                            print(f"Valid QR Code found in {filename} at frame {frame_count}: {decoded_data}")
                        except json.JSONDecodeError as jde:
                            print(f"JSON decode error for detection {idx+1} in {filename}: {jde}")
                            continue
                        except Exception as ex:
                            print(f"Unexpected error parsing decoded text in {filename}: {ex}")
                            continue

                        # **Proceed to the next video after finding at least one valid QR code**
                        break  # Exit the detection loop

                if qr_codes_found:
                    # **Write Decoded QR Codes to JSON File**
                    try:
                        with open(json_path, 'w') as json_file:
                            json.dump(qr_codes_found, json_file)
                        print(f"Saved decoded QR codes to {json_path}")
                    except Exception as e:
                        print(f"Error writing JSON file for {filename}: {e}")
                    break  # Stop processing this video after finding a valid QR code

                frame_count += 50  # Increment frame count by 50 frames

                # Skip to the next frame
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_count)

                # After 200 frames, stop the video
                if frame_count > 120:
                    # **Step 2: Try decode_partial_qr on a cropped frame before giving up**
                    print(f"QReader failed for {filename}, trying partial QR reconstruction...")
                    partial_decoded = None
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 50)
                    ret_partial, frame_partial = cap.read()
                    if ret_partial:
                        h_frame, w_frame = frame_partial.shape[:2]
                        # Crop to bottom-right region where QR monitor is located
                        qr_crop = frame_partial[int(h_frame*0.6):, int(w_frame*0.4):]
                        # Convert RGB back to BGR for decode_partial_qr (expects BGR)
                        qr_crop_bgr = cv2.cvtColor(qr_crop, cv2.COLOR_RGB2BGR)
                        try:
                            partial_result = decode_partial_qr(qr_crop_bgr, verbose=False)
                            if partial_result:
                                try:
                                    decoded_data = json.loads(partial_result)
                                    partial_decoded = [decoded_data]
                                    print(f"Partial QR reconstruction succeeded for {filename}: {decoded_data}")
                                except json.JSONDecodeError:
                                    print(f"Partial QR decoded but JSON parse failed: {partial_result}")
                        except Exception as e:
                            print(f"Partial QR reconstruction error for {filename}: {e}")

                    if partial_decoded:
                        try:
                            with open(json_path, 'w') as json_file:
                                json.dump(partial_decoded, json_file)
                            print(f"Saved partial QR result to {json_path}")
                        except Exception as e:
                            print(f"Error writing JSON file for {filename}: {e}")
                    else:
                        # **Write an Empty Array to JSON if No QR Code Found**
                        try:
                            with open(json_path, 'w') as json_file:
                                json.dump([], json_file)
                            print(f"Saved empty array to {json_path} as no QR codes were found in {filename}.")
                        except Exception as e:
                            print(f"Error writing JSON file for {filename}: {e}")
                    print(f"Stopped processing {filename} after {frame_count} frames.")
                    break

            # Release video capture for the current video
            cap.release()

    return qr_codes_output

# MySQL configuration
db_config = {
    'host': 'signlab-db',
    'user': 'user',
    'password': 'CHeZeGa85W',
    'database': 'admin_gebarenoverleg'
}

# Establish database connection
def get_db_connection():
    try:
        connection = mysql.connector.connect(**db_config)
        if connection.is_connected():
            print("Successfully connected to the database.")
            return connection
    except mysql.connector.Error as err:
        print(f"Error connecting to MySQL: {err}")
    return None

# Main execution
def main():
    # Initialize QReader instance
    qreader = QReader()

    # Establish database connection
    conn = get_db_connection()
    if not conn:
        print("Failed to connect to the database. Exiting.")
        return
    cursor = conn.cursor(dictionary=True)

    # Fetch records from studio_data where ready = '1', ordered by date descending
    sql = "SELECT * FROM studio_data WHERE ready IN ('1', '2') ORDER BY date DESC"
    # sql = "SELECT * FROM studio_data WHERE date = '2024-11-08' ORDER BY date DESC"

    try:
        cursor.execute(sql)
        result = cursor.fetchall()
    except mysql.connector.Error as err:
        print(f"Error executing SELECT query: {err}")
        cursor.close()
        conn.close()
        return

    for row in result:
        date_param = str(row['date'])
        datetime_ms = row['datetime_ms']
        count_files = row['count_files']
        camera2_files = row['camera2']
        if datetime_ms is None:
            datetime_ms = 1729098062010  # Fallback for old date
        datetime_ms = int(datetime_ms)
        current_time_ms = int(datetime.now().timestamp() * 1000)
        print(f"Record datetime_ms: {datetime_ms}, Current time ms: {current_time_ms}")

        # Execute only if 2 hours have passed since datetime_ms
        if current_time_ms > datetime_ms + 7200000:

            # Skip if the date is before the cutoff
            cutoff_date = date(2024, 10, 31)
            if row['date'] < cutoff_date:
                continue

            print(f"Processing date: {date_param}")

            # Convert date to date object
            try:
                date_param_obj = datetime.strptime(date_param, '%Y-%m-%d').date()
            except ValueError as e:
                print(f"Date conversion error for {date_param}: {e}")
                continue

            print(f"Converted date object: {date_param_obj}")

            # Directory path based on date
            directory_path = f'/web/gebarenoverleg_media/studioFiles/{date_param_obj}/raw/'

            # Check if directory exists
            if not os.path.isdir(directory_path):
                print(f"Directory does not exist: {directory_path}")
                continue
            
            #we want to count .MP4 files in the raw directory. if it matches then we continue, otherwise process the videos in dir anyway
            mp4_count = 0
            for filename in os.listdir(directory_path):
                if filename.upper().endswith('.MP4'):
                    mp4_count += 1

            videos = []
            print(mp4_count, count_files)
            if mp4_count != count_files:
                # **Process the videos and get QR code data**
                qr_codes = process_videos_in_directory(directory_path, qreader)

                # Output the results
                print("Detected QR codes:", qr_codes)

                # Fill empty JSONs from other cameras (only if all cameras have same file count)
                date_str_nodash = date_param.replace('-', '')
                fill_empty_jsons_from_other_cameras(directory_path, date_str_nodash)

            
            else:
                continue
                
                

                
            #now we are going to for loop through the json files
            for filename in os.listdir(directory_path):
                if filename.upper().endswith('.JSON'):
                    json_path = os.path.join(directory_path, filename)
                    print(json_path)
                    with open(json_path, 'r') as json_file:
                        qr_data = json.load(json_file)
                        for entry in qr_data:
                                if isinstance(entry, list) and len(entry) == 3:
                                    glosId, selectedType, time_str = entry
                                    print(f"glosId: {glosId}, selectedType: {selectedType}, time_str: {time_str}")
                                    # Add your processing logic here
                                else:
                                    print(f"Unexpected format in entry: {entry}")
                                    continue

                                # Extract incrementing number from filename
                                match = re.match(r'^([A-Z])(\d{8})_(\d+)\.json$', filename, re.IGNORECASE)
                                if match:
                                    camera, date_str, increment = match.groups()
                                    increment = int(increment)
                                else:
                                    print(f"Filename {filename} does not match expected pattern.")
                                    continue

                                # Append to videos list
                                videos.append({
                                    "filename": filename.replace('.json', '.wav'),
                                    "glosId": glosId,
                                    "selectedType": selectedType,
                                    "time_str": time_str,
                                    "camera": camera.upper(),  # Ensure camera is uppercase
                                    "date": date_str,
                                    "increment": increment
                                    })
                        
            if not videos:
                print("No valid videos found for processing.")
                continue

            # Group videos by time_str instead of glosId
            time_groups = defaultdict(list)
            for video in videos:
                time_groups[video['time_str']].append(video)

            all_groups = []
            used_files = set()
            last_selectedType = None  # Initialize last_selectedType

            for time_str, group_videos in time_groups.items():
                # Order the videos by increment
                group_videos.sort(key=lambda x: x['increment'])

                # Initialize the group
                group = {'time_str': time_str, 'selectedType': None, 'videos': []}

                for video in group_videos:
                    group['videos'].append(video)
                    if group['selectedType'] is None and video['selectedType'] is not None:
                        group['selectedType'] = video['selectedType']

                # Ensure selectedType is not None
                if group['selectedType'] is None:
                    group['selectedType'] = last_selectedType
                else:
                    last_selectedType = group['selectedType']

                all_groups.append(group)

            # Output grouped videos
            for idx, group in enumerate(all_groups):
                print(f"Group {idx + 1}:")
                print(f"  Time: {group['time_str']}, SelectedType: {group['selectedType']}")
                for video in group['videos']:
                    print(f"    Filename: {video['filename']} (Camera: {video['camera']})")

            # Process grouped videos and update the database
            for idx, group in enumerate(all_groups):
                time_str = group['time_str']
                selectedType = group['selectedType']
                group_videos = group['videos']

                # Initialize camera variables
                camera_m = None
                camera_l = None
                camera_r = None
                camera_a = None
                camera_b = None

                glosId = None  # Initialize glosId for this group

                for video in group_videos:
                    filename = video['filename']
                    camera = video['camera']
                    if camera == 'M' and filename.upper().startswith('M'):
                        camera_m = filename
                    elif camera == 'L' and filename.upper().startswith('L'):
                        camera_l = filename
                    elif camera == 'R' and filename.upper().startswith('R'):
                        camera_r = filename
                    elif camera == 'A' and filename.upper().startswith('A'):
                        camera_a = filename
                    elif camera == 'B' and filename.upper().startswith('B'):
                        camera_b = filename

                    # Assuming glosId is same across the group
                    if glosId is None:
                        glosId = video['glosId']

                # Ensure camera_m exists
                if camera_m is None:
                    print(f"No 'M' file for time {time_str}, skipping group.")
                    continue

                # Ensure no duplicate files
                current_files = set(filter(None, [camera_m, camera_l, camera_r, camera_a, camera_b]))
                if used_files.intersection(current_files):
                    print(f"Duplicate files detected in time {time_str}, skipping group.")
                    continue
                else:
                    used_files.update(current_files)

                # Ensure selectedType is not None
                if selectedType is None:
                    print(f"No 'selectedType' for time {time_str}, skipping group.")
                    continue
                
                #get date from m_file
                date_str = camera_m.split('_')[0]
                date_str = date_str.replace('M', '')
                date_str = date_str[:4] + '-' + date_str[4:6] + '-' + date_str[6:]
                datum = datetime.strptime(date_str, '%Y-%m-%d').date()

                # Check if the group is already in the database based on time_str
                sql = "SELECT * FROM matched_transcriptions WHERE time = %s AND date = %s"
                try:
                    cursor.execute(sql, (time_str, datum))
                    db_result = cursor.fetchall()
                    conn.commit()
                except mysql.connector.Error as err:
                    print(f"Error executing SELECT query for time {time_str}: {err}")
                    continue
                
                #because from march 4 on we arent going to sync with SB anymore,
                #so we need to set certain videos on zOg = extern
                #but this is not implemented yet so that's why selectedType is static set on extern
                #as temporary measure till we have dynamically implemented zOg from form_data for qR output
                selectedType = 'extern'

                if len(db_result) == 0:
                    # Insert the record
                    sql = """INSERT INTO matched_transcriptions
                            (m_file, m_transcription, l_file, r_file, b_file, a_file, 
                            l_transcription, r_transcription, b_transcription, a_transcription, 
                            added, definitive_outcome, zOg, time, date) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
                    try:
                        cursor.execute(sql, (
                            camera_m, glosId, camera_l, camera_r, camera_b, camera_a,
                            glosId, glosId, glosId, glosId, "1", glosId, selectedType,
                            time_str, datum
                        ))
                        conn.commit()
                        print(f"Inserted new record for time {time_str}.")
                    except mysql.connector.Error as err:
                        print(f"Error inserting record for time {time_str}: {err}")
                else:
                    # Initialize base SQL and parameters
                    base_sql = "UPDATE matched_transcriptions SET "
                    set_clauses = []
                    params = []

                    # Check and append each field conditionally
                    if camera_l is not None:
                        set_clauses.append("l_file = %s")
                        params.append(camera_l)

                    if camera_r is not None:
                        set_clauses.append("r_file = %s")
                        params.append(camera_r)

                    if camera_b is not None:
                        set_clauses.append("b_file = %s")
                        params.append(camera_b)

                    if camera_a is not None:
                        set_clauses.append("a_file = %s")
                        params.append(camera_a)

                    if glosId is not None:
                        set_clauses.append("m_transcription = %s")
                        params.append(glosId)

                    if glosId is not None:
                        set_clauses.append("l_transcription = %s")
                        params.append(glosId)

                    if glosId is not None:
                        set_clauses.append("r_transcription = %s")
                        params.append(glosId)

                    if glosId is not None:
                        set_clauses.append("b_transcription = %s")
                        params.append(glosId)

                    if glosId is not None:
                        set_clauses.append("a_transcription = %s")
                        params.append(glosId)

                    set_clauses.append("added = %s")
                    params.append('1')

                    if glosId is not None:
                        set_clauses.append("definitive_outcome = %s")
                        params.append(glosId)

                    if selectedType is not None:
                        set_clauses.append("zOg = %s")
                        params.append(selectedType)

                    # Ensure there is at least one field to update
                    if not set_clauses:
                        print("No fields to update.")
                        # Optionally, exit or handle accordingly
                    else:
                        # Construct final SQL
                        final_sql = base_sql + ", ".join(set_clauses) + " WHERE time = %s AND date = %s"
                        params.extend([time_str, datum])

                        # Execute the update
                        try:
                            cursor.execute(final_sql, tuple(params))
                            conn.commit()
                            print(f"Updated existing record for time {time_str}.")
                        except mysql.connector.Error as err:
                            print(f"Error updating record for time {time_str}: {err}")

                        

                        
                        
        
        
                    #finally we are going to update the ready column in the studio_data table to 2
                    #so the record is processed
                    
            #first we count MP4 files and json files, if they match then we update the ready column
            mp4_count = 0
            json_count = 0
            for filename in os.listdir(directory_path):
                if filename.upper().endswith('.MP4'):
                    mp4_count += 1
                elif filename.upper().endswith('.JSON'):
                    json_count += 1
            sql = "UPDATE studio_data SET ready = '2', count_files = %s WHERE date = %s"
            cursor.execute(sql, (mp4_count, date_param))
            conn.commit()
            print(f"Updated ready column to '2' for date {date_param} with {mp4_count} MP4 files and {json_count} JSON files.")
            
            #sometimes l_file, r_file, a_file and b_file is missing so we are going to look to next record and previous record. 
            #for example: a_file from previous record (based on time and date order) 
            # A20241108_6479.wav
            # present record is NULL
            #next record is A20241108_6481.wav
            #when those conditions are met, then we are going to update the present record with A20241108_6480.wav
            count_null = 0
            loopCount = 0
            while True:
                try:
                    sql = "SELECT * FROM matched_transcriptions WHERE date = %s ORDER BY time ASC"
                    cursor.execute(sql, (date_param_obj,))
                    result = cursor.fetchall()
                    conn.commit()
                    
                    #we want to add the sql queries in an array
                    sql_queries = []
                    for idx, row in enumerate(result):
                        sql_queries.append((idx, row))
                    
                    print(f"Found {len(result)} records.")
                    for idx, row in sql_queries:
                        # print(row)
                        updated = False
                        if row['l_file'] is None:
                            
                            if idx > 0:
                                previous_row = result[idx - 1]
                                increment = 0
                                if previous_row['m_file'] is not None:
                                    m_file = previous_row['m_file']
                                    m_file = m_file.replace('.wav', '')
                                    m_file = m_file.split('_')
                                    m_file = m_file[1]
                                    m_file = int(m_file)
                                    if row['m_file'] is not None:
                                        m_file_present = row['m_file']
                                        m_file_present = m_file_present.replace('.wav', '')
                                        m_file_present = m_file_present.split('_')
                                        m_file_present = m_file_present[1]
                                        m_file_present = int(m_file_present)
                                        if m_file is not None and m_file_present is not None:
                                            increment = m_file_present - m_file
                                    
                                    
                                    if increment ==0:
                                        continue
                                    
                                    if previous_row['l_file'] is not None:
                                        l_file = previous_row['l_file']
                                        l_file = l_file.replace('.wav', '')

                                        # Modify l_file by adding +1 to the increment
                                        l_file = l_file.split('_')
                                        l_file[1] = str(int(l_file[1]) + increment)
                                        l_file = '_'.join(l_file)
                                        l_file = l_file + '.wav'

                                        sql = "UPDATE matched_transcriptions SET l_file = %s WHERE id = %s"
                                        cursor.execute(sql, (l_file, row['id']))
                                        conn.commit()
                                        print(f"Updated l_file for time {row['time']} with {l_file}.")
                                        updated = True
                        if row['r_file'] is None:
                            if idx > 0:
                                previous_row = result[idx - 1]
                                increment = 0
                                if previous_row['m_file'] is not None:
                                    m_file = previous_row['m_file']
                                    m_file = m_file.replace('.wav', '')
                                    m_file = m_file.split('_')
                                    m_file = m_file[1]
                                    m_file = int(m_file)
                                    if row['m_file'] is not None:
                                        m_file_present = row['m_file']
                                        m_file_present = m_file_present.replace('.wav', '')
                                        m_file_present = m_file_present.split('_')
                                        m_file_present = m_file_present[1]
                                        m_file_present = int(m_file_present)
                                        if m_file is not None and m_file_present is not None:
                                            increment = m_file_present - m_file
                                    
                                    
                                    if increment ==0:
                                        continue                    
                                    if previous_row['r_file'] is not None:                         
                                        r_file = previous_row['r_file']
                                        r_file = r_file.replace('.wav', '')

                                        # Modify r_file by adding +1 to the increment
                                        r_file = r_file.split('_')
                                        r_file[1] = str(int(r_file[1]) + increment)
                                        r_file = '_'.join(r_file)
                                        r_file = r_file + '.wav'

                                        sql = "UPDATE matched_transcriptions SET r_file = %s WHERE id = %s"
                                        cursor.execute(sql, (r_file, row['id']))
                                        conn.commit()
                                        print(f"Updated r_file for time {row['time']} with {r_file}.")
                                        updated = True

                        if row['a_file'] is None:
                            print("updating a_file", row['time'], row['m_file'])
                            if idx > 0:
                                previous_row = result[idx - 1]
                                increment = 0
                                if previous_row['m_file'] is not None:
                                    m_file = previous_row['m_file']
                                    m_file = m_file.replace('.wav', '')
                                    m_file = m_file.split('_')
                                    m_file = m_file[1]
                                    m_file = int(m_file)
                                    if row['m_file'] is not None:
                                        m_file_present = row['m_file']
                                        m_file_present = m_file_present.replace('.wav', '')
                                        m_file_present = m_file_present.split('_')
                                        m_file_present = m_file_present[1]
                                        m_file_present = int(m_file_present)
                                        if m_file is not None and m_file_present is not None:
                                            increment = m_file_present - m_file
                                    
                                    
                                    if increment ==0:
                                        continue                   
                                    if previous_row['a_file'] is not None:             
                                        a_file = previous_row['a_file']
                                        a_file = a_file.replace('.wav', '')
                                        # Modify a_file by adding +1 to the increment
                                        a_file = a_file.split('_')

                                        #remove .wav from a_file
                                        a_file[0] = a_file[0]
                                        a_file[1] = str(int(a_file[1]) + increment)
                                        a_file = '_'.join(a_file)
                                        a_file = a_file + '.wav'

                                        sql = "UPDATE matched_transcriptions SET a_file = %s WHERE id = %s"
                                        cursor.execute(sql, (a_file, row['id']))
                                        conn.commit()
                                        print(f"Updated a_file for time {row['time']} with {a_file}.")
                                        updated = True
                        if row['b_file'] is None:
                            if idx > 0:
                                previous_row = result[idx - 1]
                                next_row = result[idx + 1]
                                #TODO: we should look to m_file of present record and m_file of previous record and calculate the difference then add the difference to b_file of present record
                                increment = 0
                                if previous_row['m_file'] is not None:
                                    m_file = previous_row['m_file']
                                    m_file = m_file.replace('.wav', '')
                                    m_file = m_file.split('_')
                                    m_file = m_file[1]
                                    m_file = int(m_file)
                                    if row['m_file'] is not None:
                                        m_file_present = row['m_file']
                                        m_file_present = m_file_present.replace('.wav', '')
                                        m_file_present = m_file_present.split('_')
                                        m_file_present = m_file_present[1]
                                        m_file_present = int(m_file_present)
                                        if m_file is not None and m_file_present is not None:
                                            increment = m_file_present - m_file
                                    
                                    
                                    if increment ==0:
                                        continue
                                    print(m_file)
                                    if previous_row['b_file'] is not None:             
                                        b_file = previous_row['b_file']
                                        b_file = b_file.replace('.wav', '')

                                        # Modify b_file by adding +1 to the increment
                                        b_file = b_file.split('_')
                                        b_file[1] = str(int(b_file[1]) + increment)
                                        b_file = '_'.join(b_file)
                                        b_file = b_file + '.wav'

                                        sql = "UPDATE matched_transcriptions SET b_file = %s WHERE id = %s"
                                        cursor.execute(sql, (b_file, row['id']))
                                        conn.commit()
                                        print(f"Updated b_file for time {row['time']} with {b_file}.")
                                        updated = True
                        #update sql_queries with new values from mysql
                        
                        if updated:
                            sql = "SELECT * FROM matched_transcriptions WHERE date = %s ORDER BY time ASC"
                            cursor.execute(sql, (date_param_obj,))
                            result = cursor.fetchall()
                            conn.commit()
                            
                            #we replace sql_queries with new values
                            sql_queries = []
                            for idx, row in enumerate(result):
                                sql_queries.append((idx, row))
                                

                            #count the number of NULL values in rows at a_file, b_file, l_file, r_file
                            count_null = sum(row['a_file'] is None or row['b_file'] is None or row['l_file'] is None or row['r_file'] is None for idx, row in sql_queries)
                            print(f"Number of NULL values: {count_null}")
                        
                            if count_null == 0:
                                print("All files have been updated. Exiting.")
                                break
                        
                    if count_null == 0:
                        print("All files have been updated. Exiting.")
                        break
                            
                except mysql.connector.Error as err:
                    print(f"Error updating missing files: {err}")
                    
            print("COUNTING SHIT")
            #we compare matched_transcriptions count with count_files from studio_data
            # Compare counts of matched_transcriptions with count_files from studio_data
            try:
                count_sql = "SELECT COUNT(*) AS count FROM matched_transcriptions WHERE date = %s"
                count_sql += """
                    AND (l_file IS  NULL
                    OR m_file IS  NULL
                    OR a_file IS  NULL
                    OR b_file IS  NULL
                    OR r_file IS  NULL)
                """ 
                cursor.execute(count_sql, (date_param_obj,))
                result = cursor.fetchone()
                matched_count = result['count']
                
                print(matched_count)
                if matched_count <= 4:
                                sql = "UPDATE studio_data SET ready = '3'WHERE date = %s"
                                cursor.execute(sql, (date_param,))
                                conn.commit()
                    #break the while true loop
                        
                                break
                else:
                    print(f"Mismatch for date {date_param}: matched_transcriptions has {matched_count} rows, but count_files is {camera2_files}.")
            except mysql.connector.Error as err:
                print(f"Error counting rows for date {date_param}: {err}")
                    
                
                
                
                
    
                

    # Close the cursor and connection
    try:
        cursor.close()
    except Exception as e:
        print(f"Error closing cursor: {e}")
    try:
        conn.close()
    except Exception as e:
        print(f"Error closing connection: {e}")

if __name__ == "__main__":
    # Check if another instance is running
    script_name = os.path.basename(__file__)
    script_name = os.path.splitext(script_name)[0]
    if is_already_running(script_name):
        print(f"Another instance of {script_name} is already running. Exiting.")
        sys.exit(0)
    main()
    #when finished we are going to remove all png files
    for filename in os.listdir("/Users/signlab/drs/qr"):
        if filename.upper().endswith('.PNG'):
            os.remove(os.path.join("/Users/signlab/drs/qr", filename))
            print(f"Removed {filename}")
