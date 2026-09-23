#!/usr/bin/env python3
"""One-off QR rescan for specific dates. Overwrites existing JSON files."""
import cv2
import os
import sys
import json
import re
import requests
from datetime import datetime
from multiprocessing import Pool
from qreader import QReader

STUDIO_FILES_BASE = "/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles"
API_URL = "https://signcollect.nl/qr/qrResultReceiver.php"
MAX_WORKERS = 4

DATES_TO_RESCAN = ["2026-02-10", "2026-02-13", "2026-02-16"]

_qreader = None

def get_qreader():
    global _qreader
    if _qreader is None:
        _qreader = QReader()
    return _qreader

def parse_filename(filename):
    match = re.match(r'^([ABLMR])(\d{4})(\d{2})(\d{2})_(\d+)\.(jpg|mp4)$', filename, re.IGNORECASE)
    if match:
        camera = match.group(1).upper()
        year, month, day, increment = match.group(2), match.group(3), match.group(4), match.group(5)
        date_str = f"{year}-{month}-{day}"
        wav_filename = f"{camera}{year}{month}{day}_{increment}.wav"
        return camera, date_str, wav_filename
    return None, None, None

def send_to_api(camera, wav_filename, glos_id, qr_type, time_str, date_str):
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
    except Exception:
        pass
    return None

def process_file(args):
    date_dir, filename, thumbnails_dir, raw_dir = args
    image_path = os.path.join(thumbnails_dir, filename)
    json_filename = os.path.splitext(filename)[0] + '.json'
    json_path = os.path.join(raw_dir, json_filename)

    try:
        qr_data = extract_qr_from_image(image_path)
    except Exception as e:
        return ("error", filename, str(e))

    if qr_data and len(qr_data) == 3:
        glos_id, qr_type, time_str = qr_data
        try:
            with open(json_path, 'w') as f:
                json.dump(qr_data, f)
        except IOError:
            pass

        camera, date_str, wav_filename = parse_filename(filename)
        api_sent = False
        if camera and date_str and wav_filename:
            success, _ = send_to_api(camera, wav_filename, glos_id, qr_type, time_str, date_str)
            api_sent = success

        return ("qr_found", filename, qr_data, api_sent)
    else:
        try:
            with open(json_path, 'w') as f:
                json.dump([], f)
        except IOError:
            pass
        return ("no_qr", filename, None, False)

def main():
    print(f"QR Rescan - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Dates to rescan: {DATES_TO_RESCAN}")

    work_items = []
    for date_dir in DATES_TO_RESCAN:
        thumbnails_dir = os.path.join(STUDIO_FILES_BASE, date_dir, "thumbnails")
        raw_dir = os.path.join(STUDIO_FILES_BASE, date_dir, "raw")

        if not os.path.exists(thumbnails_dir):
            print(f"  {date_dir}: no thumbnails/ directory, skipping")
            continue

        os.makedirs(raw_dir, exist_ok=True)
        files = [f for f in os.listdir(thumbnails_dir) if f.lower().endswith('.jpg')]
        print(f"  {date_dir}: {len(files)} thumbnails to scan")

        for filename in files:
            work_items.append((date_dir, filename, thumbnails_dir, raw_dir))

    if not work_items:
        print("No files to process.")
        return

    print(f"\nProcessing {len(work_items)} files with {MAX_WORKERS} workers...\n")

    stats = {"qr_found": 0, "no_qr": 0, "error": 0, "api_sent": 0}

    with Pool(processes=MAX_WORKERS) as pool:
        for i, result in enumerate(pool.imap(process_file, work_items), 1):
            status = result[0]
            filename = result[1]

            if status == "qr_found":
                qr_data, api_sent = result[2], result[3]
                stats["qr_found"] += 1
                if api_sent:
                    stats["api_sent"] += 1
                if i % 100 == 0 or i == len(work_items):
                    print(f"  [{i}/{len(work_items)}] Progress - QR: {stats['qr_found']}, No QR: {stats['no_qr']}, Errors: {stats['error']}")
            elif status == "no_qr":
                stats["no_qr"] += 1
                if i % 100 == 0 or i == len(work_items):
                    print(f"  [{i}/{len(work_items)}] Progress - QR: {stats['qr_found']}, No QR: {stats['no_qr']}, Errors: {stats['error']}")
            elif status == "error":
                stats["error"] += 1
                print(f"  [{i}/{len(work_items)}] ERROR: {filename} - {result[2]}")

    print(f"\nRescan complete!")
    print(f"  QR found: {stats['qr_found']} (API sent: {stats['api_sent']})")
    print(f"  No QR: {stats['no_qr']}")
    print(f"  Errors: {stats['error']}")

if __name__ == "__main__":
    main()
