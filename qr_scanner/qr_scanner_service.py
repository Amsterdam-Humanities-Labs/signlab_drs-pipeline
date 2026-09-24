#!/usr/bin/env python3
"""
QR Scanner Service - Hourly automated QR code scanning with multiprocessing.
Scans all date directories for new thumbnail images, detects QR codes,
saves JSON results, and sends new findings to the API.

Runs continuously with 1-hour intervals between scans.
Uses 4 worker processes for parallel QR detection.
"""
import cv2
import os
import sys
import json
import re
import time
import requests
import numpy as np
from datetime import datetime, timedelta
from multiprocessing import Pool
from qreader import QReader
from pyzbar import pyzbar as pyzbar_mod
from collections import defaultdict

# Add shared directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'shared'))
from signcollect_monitor import SignCollectMonitor  # Import the monitor client
from server_config import server_url

# Initialize the monitor
qr_monitor = SignCollectMonitor(
    client_id='drs-qr-scanner',
    client_name='DRS QR Scanner',
    description='QR code detection and scanning service',
    heartbeat_interval=3600
)

# Configuration
STUDIO_FILES_BASE = "/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles"
SCANNED_STATE_FILE = "/Users/signlab/drs/qr/scanned_files.json"
API_URL = server_url("qr/qrResultReceiver.php")
SCAN_INTERVAL = 900  # 1 hour in seconds
MAX_WORKERS = 4

# Global QReader instance per process
_qreader = None


def get_qreader():
    """Get or create QReader instance for this process."""
    global _qreader
    if _qreader is None:
        _qreader = QReader()
    return _qreader


def timestamp():
    """Return current timestamp string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_scanned_state():
    """Load the scanned files state from disk."""
    if os.path.exists(SCANNED_STATE_FILE):
        try:
            with open(SCANNED_STATE_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"[{timestamp()}] Warning: Could not load scanned state: {e}")
    return {}


def save_scanned_state(state):
    """Save the scanned files state to disk."""
    try:
        with open(SCANNED_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except IOError as e:
        print(f"[{timestamp()}] Error saving scanned state: {e}")


def get_date_directories():
    """Get date directories from the past 7 days (YYYY-MM-DD format) from studioFiles."""
    if not os.path.exists(STUDIO_FILES_BASE):
        print(f"[{timestamp()}] Warning: Studio files base not found: {STUDIO_FILES_BASE}")
        return []

    # Scan directories from the past 7 days
    date_dirs = []
    for days_ago in range(7):
        date_str = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        date_path = os.path.join(STUDIO_FILES_BASE, date_str)
        if os.path.isdir(date_path):
            date_dirs.append(date_str)

    if not date_dirs:
        print(f"[{timestamp()}] No date directories found in the past 7 days")

    return date_dirs


def parse_filename(filename):
    """Parse filename to extract camera and date.
    Example: M20251216_8405.jpg -> camera=M, date=2025-12-16
    """
    match = re.match(r'^([ABLMR])(\d{4})(\d{2})(\d{2})_(\d+)\.(jpg|mp4)$', filename, re.IGNORECASE)
    if match:
        camera = match.group(1).upper()
        year = match.group(2)
        month = match.group(3)
        day = match.group(4)
        increment = match.group(5)
        date_str = f"{year}-{month}-{day}"
        wav_filename = f"{camera}{year}{month}{day}_{increment}.wav"
        return camera, date_str, wav_filename
    return None, None, None


def send_to_api(camera, wav_filename, glos_id, qr_type, time_str, date_str):
    """Send QR result to the API endpoint."""
    payload = {
        "camera": camera,
        "filename": wav_filename,
        "glosId": glos_id,
        "type": qr_type,
        "time": time_str,
        "date": date_str
    }

    try:
        response = requests.post(API_URL, json=payload, timeout=10)
        return response.status_code == 200, response.text
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────────────────────
# PARTIAL QR DECODE (reconstructs missing top-right finder pattern)
# ─────────────────────────────────────────────────────────────────────────────

FINDER_PATTERN = np.array([
    [1,1,1,1,1,1,1],[1,0,0,0,0,0,1],[1,0,1,1,1,0,1],
    [1,0,1,1,1,0,1],[1,0,1,1,1,0,1],[1,0,0,0,0,0,1],[1,1,1,1,1,1,1],
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
        s for s in squares if s != tl
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


def reconstruct_rotated(img, tl_finder, bl_finder):
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


def decode_partial_qr(image_input):
    if isinstance(image_input, str):
        img = cv2.imread(image_input)
    else:
        img = image_input.copy()
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = img.shape[:2]
    # Step 1: direct pyzbar decode
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
        result = reconstruct_rotated(img, tl_finder, bl_finder)
        if result:
            return result
    return None


def fill_empty_jsons_from_other_cameras(raw_dir, date_str):
    """Cross-camera fallback: fill empty JSONs from other cameras at same index.
    Only applies when all cameras have the same number of files."""
    date_str_nodash = date_str.replace('-', '')
    camera_files = defaultdict(list)
    for f in os.listdir(raw_dir):
        if f.upper().endswith('.MP4') or f.lower().endswith('.jpg'):
            if date_str_nodash in f:
                cam = f[0].upper()
                camera_files[cam].append(f)
    if len(camera_files) < 2:
        return 0
    counts = [len(files) for files in camera_files.values()]
    if len(set(counts)) != 1:
        print(f"[{timestamp()}] Camera file counts differ ({dict((k, len(v)) for k, v in camera_files.items())}), skipping cross-camera fill.")
        return 0
    for cam in camera_files:
        camera_files[cam].sort(key=lambda f: int(os.path.splitext(f)[0].split('_')[1]))
    file_count = counts[0]
    filled = 0
    for idx in range(file_count):
        for cam in camera_files:
            json_filename = os.path.splitext(camera_files[cam][idx])[0] + '.json'
            json_path = os.path.join(raw_dir, json_filename)
            if not os.path.exists(json_path):
                continue
            with open(json_path, 'r') as f:
                data = json.load(f)
            if data:
                continue
            for other_cam in camera_files:
                if other_cam == cam:
                    continue
                other_json = os.path.splitext(camera_files[other_cam][idx])[0] + '.json'
                other_json_path = os.path.join(raw_dir, other_json)
                if not os.path.exists(other_json_path):
                    continue
                with open(other_json_path, 'r') as f:
                    other_data = json.load(f)
                if other_data and len(other_data) == 3:
                    with open(json_path, 'w') as f:
                        json.dump(other_data, f)
                    # Send to API
                    camera, file_date_str, wav_filename = parse_filename(json_filename.replace('.json', '.jpg'))
                    if camera and file_date_str and wav_filename:
                        glos_id, qr_type, time_str = other_data
                        success, _ = send_to_api(camera, wav_filename, glos_id, qr_type, time_str, file_date_str)
                        api_status = "sent" if success else "API failed"
                    else:
                        api_status = "no API (parse failed)"
                    print(f"[{timestamp()}] Filled {json_filename} from {other_json} (camera {other_cam}), {api_status}")
                    filled += 1
                    break
    return filled


# ─────────────────────────────────────────────────────────────────────────────

def extract_qr_from_image(image_path):
    """Extract QR code data from a thumbnail image.
    Step 1: QReader detection
    Step 2: decode_partial_qr fallback (reconstructs missing TR finder pattern)
    """
    qreader = get_qreader()
    frame = cv2.imread(image_path)
    if frame is None:
        return None

    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    # Step 1: QReader
    try:
        detections = qreader.detect(image=frame_rgb)
        for detection in detections:
            x1, y1, x2, y2 = detection['bbox_xyxy']
            x1, y1, x2, y2 = int(x1-10), int(y1-10), int(x2+10), int(y2+10)

            cropped = frame_rgb[max(0,y1):y2, max(0,x1):x2]
            decoded = qreader.detect_and_decode(image=cropped)

            if decoded and decoded[0]:
                return json.loads(decoded[0])
    except Exception:
        pass

    # Step 2: decode_partial_qr on cropped QR region
    try:
        h, w = frame.shape[:2]
        qr_crop = frame[int(h*0.6):, int(w*0.4):]
        partial_result = decode_partial_qr(qr_crop)
        if partial_result:
            return json.loads(partial_result)
    except Exception:
        pass

    return None


def process_file(args):
    """Process a single file - designed for multiprocessing.

    Returns: (state_key, result_dict, status, qr_data)
    - status: "skipped_json_exists", "new_qr_found", "no_qr", "error"
    """
    date_dir, filename, thumbnails_dir, raw_dir = args
    state_key = f"{date_dir}/{filename}"

    image_path = os.path.join(thumbnails_dir, filename)
    json_filename = os.path.splitext(filename)[0] + '.json'
    json_path = os.path.join(raw_dir, json_filename)

    # If JSON already exists, it means it was already processed and sent to API
    # Just mark as scanned without sending to API again
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r') as f:
                existing_data = json.load(f)
            qr_found = existing_data and len(existing_data) == 3
            return (state_key, {
                "scanned_at": datetime.now().isoformat(),
                "qr_found": qr_found,
                "api_sent": True  # Assume already sent since JSON exists
            }, "skipped_json_exists", existing_data if qr_found else None)
        except (json.JSONDecodeError, IOError):
            pass  # If we can't read it, process again

    # Extract QR from image
    try:
        qr_data = extract_qr_from_image(image_path)
    except Exception as e:
        return (state_key, None, "error", str(e))

    if qr_data and len(qr_data) == 3:
        glos_id, qr_type, time_str = qr_data

        # Save JSON locally
        try:
            with open(json_path, 'w') as f:
                json.dump(qr_data, f)
        except IOError:
            pass

        # Send to API (only for new files)
        api_sent = False
        camera, date_str, wav_filename = parse_filename(filename)
        if camera and date_str and wav_filename:
            success, _ = send_to_api(camera, wav_filename, glos_id, qr_type, time_str, date_str)
            api_sent = success

        return (state_key, {
            "scanned_at": datetime.now().isoformat(),
            "qr_found": True,
            "api_sent": api_sent
        }, "new_qr_found", qr_data)
    else:
        # No QR found - save empty JSON
        try:
            with open(json_path, 'w') as f:
                json.dump([], f)
        except IOError:
            pass

        return (state_key, {
            "scanned_at": datetime.now().isoformat(),
            "qr_found": False,
            "api_sent": False
        }, "no_qr", None)


def scan_all_directories():
    """Scan all date directories for new thumbnails using multiprocessing."""
    print(f"[{timestamp()}] Starting scan of all date directories...")

    date_dirs = get_date_directories()

    if not date_dirs:
        print(f"[{timestamp()}] No date directories found")
        return

    print(f"[{timestamp()}] Found {len(date_dirs)} date directories")

    # Collect all work items across all directories
    work_items = []

    for date_dir in date_dirs:
        thumbnails_dir = os.path.join(STUDIO_FILES_BASE, date_dir, "thumbnails")
        raw_dir = os.path.join(STUDIO_FILES_BASE, date_dir, "raw")

        if not os.path.exists(thumbnails_dir):
            continue

        # Ensure raw directory exists
        if not os.path.exists(raw_dir):
            try:
                os.makedirs(raw_dir, exist_ok=True)
            except OSError:
                continue

        try:
            files = [f for f in os.listdir(thumbnails_dir) if f.lower().endswith('.jpg')]
        except OSError:
            continue

        for filename in files:
            work_items.append((date_dir, filename, thumbnails_dir, raw_dir))

    if not work_items:
        print(f"[{timestamp()}] Scan complete: No new files to process")
        return

    print(f"[{timestamp()}] Processing {len(work_items)} new files with {MAX_WORKERS} workers...")

    stats = {
        "skipped_json_exists": 0,
        "new_qr_found": 0,
        "no_qr": 0,
        "error": 0,
        "api_sent": 0
    }

    # Process with multiprocessing pool
    with Pool(processes=MAX_WORKERS) as pool:
        for i, result in enumerate(pool.imap(process_file, work_items), 1):
            state_key, state_data, status, qr_data = result

            if state_data and state_data.get("api_sent"):
                stats["api_sent"] += 1

            stats[status] = stats.get(status, 0) + 1

            if status == "new_qr_found":
                print(f"[{timestamp()}] [{i}/{len(work_items)}] {state_key}: QR found -> {qr_data}")
            elif status == "error":
                print(f"[{timestamp()}] [{i}/{len(work_items)}] {state_key}: Error - {qr_data}")

    # Step 3: Cross-camera fill for any remaining empty JSONs
    cross_filled = 0
    for date_dir in date_dirs:
        raw_dir = os.path.join(STUDIO_FILES_BASE, date_dir, "raw")
        if os.path.exists(raw_dir):
            cross_filled += fill_empty_jsons_from_other_cameras(raw_dir, date_dir)

    # Print summary
    print(f"[{timestamp()}] Scan complete: {len(work_items)} processed")
    print(f"[{timestamp()}]   - New QR found: {stats['new_qr_found']} (API sent: {stats['api_sent']})")
    print(f"[{timestamp()}]   - No QR: {stats['no_qr']}")
    if cross_filled > 0:
        print(f"[{timestamp()}]   - Cross-camera filled: {cross_filled}")
    print(f"[{timestamp()}]   - Skipped (JSON exists): {stats['skipped_json_exists']}")
    if stats['error'] > 0:
        print(f"[{timestamp()}]   - Errors: {stats['error']}")


def main():
    """Main service loop."""
    # Register with monitoring system
    qr_monitor.register()

    print(f"[{timestamp()}] QR Scanner Service starting...")
    print(f"[{timestamp()}] Base directory: {STUDIO_FILES_BASE}")
    print(f"[{timestamp()}] State file: {SCANNED_STATE_FILE}")
    print(f"[{timestamp()}] Workers: {MAX_WORKERS}")
    print(f"[{timestamp()}] Scan interval: {SCAN_INTERVAL} seconds ({SCAN_INTERVAL/3600:.1f} hours)")

    while True:
        try:
            # Send heartbeat at start of each cycle
            qr_monitor.send_heartbeat()

            scan_all_directories()
        except Exception as e:
            print(f"[{timestamp()}] Error during scan: {e}")

        print(f"[{timestamp()}] Next scan in {SCAN_INTERVAL/60:.0f} minutes...")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
