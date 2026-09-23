#!/usr/bin/env python3
"""Send existing JSON files from 2025-12-17 raw directory to API."""
import os
import json
import re
import requests

RAW_DIR = "/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles/2025-12-17/raw"
API_URL = "https://signcollect.nl/qr/qrResultReceiver.php"

def parse_filename(filename):
    """Parse filename -> (camera, date, wav_filename)"""
    # Pattern: A20251217_1234.json
    match = re.match(r'^([ABLMR])(\d{4})(\d{2})(\d{2})_(\d+)\.json$', filename, re.I)
    if match:
        camera = match.group(1).upper()
        date_str = f"{match.group(2)}-{match.group(3)}-{match.group(4)}"
        wav = f"{camera}{match.group(2)}{match.group(3)}{match.group(4)}_{match.group(5)}.wav"
        return camera, date_str, wav
    return None, None, None

def send_to_api(camera, wav_filename, glos_id, qr_type, time_str, date_str):
    """Send QR result to API."""
    payload = {
        "camera": camera,
        "filename": wav_filename,
        "glosId": glos_id,
        "type": qr_type,
        "time": time_str,
        "date": date_str
    }
    response = requests.post(API_URL, json=payload, timeout=10)
    return response.status_code == 200, response.text

def main():
    files = sorted([f for f in os.listdir(RAW_DIR) if f.endswith('.json')])
    success = failed = skipped = 0

    for f in files:
        path = os.path.join(RAW_DIR, f)
        with open(path) as fp:
            data = json.load(fp)

        # Skip empty arrays silently
        if not data or len(data) != 3:
            skipped += 1
            continue

        camera, date_str, wav = parse_filename(f)
        if not camera:
            skipped += 1
            continue

        glos_id, qr_type, time_str = data
        ok, resp = send_to_api(camera, wav, glos_id, qr_type, time_str, date_str)

        if ok:
            print(f"OK: {f} -> {data}")
            success += 1
        else:
            print(f"FAIL: {f} -> {resp[:50]}")
            failed += 1

    print(f"\nDone: {success} sent, {failed} failed, {skipped} skipped")

if __name__ == "__main__":
    main()
