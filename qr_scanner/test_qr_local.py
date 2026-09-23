#!/usr/bin/env python3
"""
Test QR recognition on thumbnail images with multiprocessing.
Scans thumbnails directory, saves JSON files to raw directory,
and sends results to QrResultReceiver API.

Usage:
    python3 test_qr_local.py          # Quick test (10 files)
    python3 test_qr_local.py --all    # Process all files
    python3 test_qr_local.py -n 50    # Process 50 files
"""
import cv2
import os
import json
import argparse
import re
import requests
from multiprocessing import Pool
from qreader import QReader

BASE_DIR = "/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles/2025-12-16"
THUMBNAILS_DIR = os.path.join(BASE_DIR, "thumbnails")
RAW_DIR = os.path.join(BASE_DIR, "raw")
API_URL = "https://signcollect.nl/qr/qrResultReceiver.php"
DEFAULT_LIMIT = 10
MAX_WORKERS = 4

# Global QReader instance per process
_qreader = None

def get_qreader():
    """Get or create QReader instance for this process."""
    global _qreader
    if _qreader is None:
        _qreader = QReader()
    return _qreader

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

def extract_qr_from_image(image_path):
    """Extract QR code data from a thumbnail image."""
    qreader = get_qreader()
    frame = cv2.imread(image_path)
    if frame is None:
        return None

    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    try:
        detections = qreader.detect(image=frame_rgb)
        for detection in detections:
            x1, y1, x2, y2 = detection['bbox_xyxy']
            x1, y1, x2, y2 = int(x1-10), int(y1-10), int(x2+10), int(y2+10)

            cropped = frame_rgb[max(0,y1):y2, max(0,x1):x2]
            decoded = qreader.detect_and_decode(image=cropped)

            if decoded and decoded[0]:
                return json.loads(decoded[0])
    except Exception as e:
        pass

    return None

def process_file(args):
    """Process a single file - designed for multiprocessing."""
    filename, thumbnails_dir, raw_dir, send_api, force_api = args
    image_path = os.path.join(thumbnails_dir, filename)
    json_filename = os.path.splitext(filename)[0] + '.json'
    json_path = os.path.join(raw_dir, json_filename)

    # Skip if JSON already exists (unless force_api is set)
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r') as f:
                existing_data = json.load(f)
            if existing_data and len(existing_data) == 3:
                # If force_api, send existing data to API
                if force_api and send_api:
                    glos_id, qr_type, time_str = existing_data
                    camera, date_str, wav_filename = parse_filename(filename)
                    if camera and date_str and wav_filename:
                        success, response = send_to_api(camera, wav_filename, glos_id, qr_type, time_str, date_str)
                        api_status = "API OK" if success else f"API ERR: {response[:50]}"
                        return (filename, True, existing_data, api_status)
                return (filename, True, existing_data, "SKIPPED (exists)")
        except:
            pass  # If we can't read it, process again

    qr_data = extract_qr_from_image(image_path)

    if qr_data and len(qr_data) == 3:
        glos_id, qr_type, time_str = qr_data

        # Save JSON locally
        with open(json_path, 'w') as f:
            json.dump(qr_data, f)

        # Send to API if enabled
        api_status = None
        if send_api:
            camera, date_str, wav_filename = parse_filename(filename)
            if camera and date_str and wav_filename:
                success, response = send_to_api(camera, wav_filename, glos_id, qr_type, time_str, date_str)
                api_status = "API OK" if success else f"API ERR: {response[:50]}"

        return (filename, True, qr_data, api_status)
    else:
        with open(json_path, 'w') as f:
            json.dump([], f)
        return (filename, False, None, None)

def main():
    parser = argparse.ArgumentParser(description='Test QR recognition on thumbnail images')
    parser.add_argument('--all', action='store_true', help='Process all files')
    parser.add_argument('-n', type=int, default=DEFAULT_LIMIT, help='Number of files to process')
    parser.add_argument('-w', type=int, default=MAX_WORKERS, help='Number of worker processes')
    parser.add_argument('--no-api', action='store_true', help='Skip sending to API')
    parser.add_argument('--force-api', action='store_true', help='Send existing JSON data to API (no skip)')
    args = parser.parse_args()

    send_api = not args.no_api
    force_api = args.force_api

    files = sorted([f for f in os.listdir(THUMBNAILS_DIR) if f.lower().endswith('.jpg')])

    if not args.all:
        files = files[:args.n]

    print(f"Processing {len(files)} thumbnail images with {args.w} workers")
    print(f"From: {THUMBNAILS_DIR}")
    print(f"To: {RAW_DIR}")
    print(f"API: {'Enabled -> ' + API_URL if send_api else 'Disabled'}")
    if force_api:
        print(f"Force API: Sending all existing JSON data to API\n")
    else:
        print()

    # Prepare arguments for each file
    work_items = [(f, THUMBNAILS_DIR, RAW_DIR, send_api, force_api) for f in files]

    success = 0
    failed = 0
    skipped = 0
    api_success = 0

    with Pool(processes=args.w) as pool:
        for i, result in enumerate(pool.imap(process_file, work_items), 1):
            filename, ok, qr_data, api_status = result
            if ok:
                if api_status and "SKIPPED" in api_status:
                    print(f"[{i}/{len(files)}] {filename}... SKIPPED")
                    skipped += 1
                else:
                    status_msg = f"OK -> {qr_data}"
                    if api_status:
                        status_msg += f" | {api_status}"
                        if "API OK" in api_status:
                            api_success += 1
                    print(f"[{i}/{len(files)}] {filename}... {status_msg}")
                    success += 1
            else:
                print(f"[{i}/{len(files)}] {filename}... FAIL")
                failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {success} success, {failed} failed, {skipped} skipped out of {len(files)} files")
    if send_api:
        print(f"API sent: {api_success} successful")
    print(f"JSON files saved to: {RAW_DIR}")

if __name__ == "__main__":
    main()
