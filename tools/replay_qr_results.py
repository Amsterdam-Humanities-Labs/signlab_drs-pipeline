#!/usr/bin/env python3
"""
Replay already-decoded QR results to qrResultReceiver.php.

The scanner writes its decode to raw/<clip>.json AND posts it to the API. When
the API is unreachable the local JSON is still written, so the QR data survives -
but nothing reaches matched_transcriptions and the recordings never appear on
studioIndex. That is what happened while /web/qr was deleted.

This re-posts the JSONs that are already on disk, in exactly the shape
qr_scanner_service.py::send_to_api() sends, so no re-scanning is needed.

    raw/L20260824_6288.json  ->  ["9999", "test", "09:31:36"]
    POST {"camera":"L","filename":"L20260824_6288.wav","glosId":"9999",
          "type":"test","time":"09:31:36","date":"2026-08-24"}

The receiver identifies a recording by (time, date) and upserts, so re-running is
safe: the first camera for a take inserts the row, the rest update it, and a
repeat run just rewrites the same values.

Usage:
    python3 tools/replay_qr_results.py 2026-08-24 [2026-08-18 ...]
                                       [--dry-run] [--limit N] [--delay 0.05]
                                       [--cameras LMRAB]
"""
import os
import sys
import json
import time
import glob
import argparse
import collections
import requests

BASE_DIR = "/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles"
API_URL = "https://signcollect.nl/qr/qrResultReceiver.php"


def collect(date, cameras):
    """Return [(camera, wav_filename, glosId, type, time)] for one date, in clip order."""
    raw_dir = os.path.join(BASE_DIR, date, "raw")
    if not os.path.isdir(raw_dir):
        return None

    results = []
    for path in sorted(glob.glob(os.path.join(raw_dir, "*.json"))):
        stem = os.path.basename(path)[:-5]
        camera = stem[0].upper()
        if camera not in cameras:
            continue
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (ValueError, OSError):
            continue
        # An empty [] means the scanner found no QR in that clip - nothing to send.
        if not data or len(data) < 3:
            continue
        glos_id, qr_type, time_str = data[0], data[1], data[2]
        results.append((camera, stem + ".wav", str(glos_id), str(qr_type), str(time_str)))
    return results


def send(entry, date, session, attempts=2):
    """POST one decoded result. Returns (ok, action)."""
    camera, filename, glos_id, qr_type, time_str = entry
    payload = {
        "camera": camera,
        "filename": filename,
        "glosId": glos_id,
        "type": qr_type,
        "time": time_str,
        "date": date,
    }
    for attempt in range(1, attempts + 1):
        try:
            r = session.post(API_URL, json=payload, timeout=30)
            if r.status_code == 200:
                try:
                    body = r.json()
                except ValueError:
                    return False, "bad-json"
                if body.get("success"):
                    return True, body.get("action", "?")
                return False, str(body.get("error"))[:60]
            return False, f"HTTP {r.status_code}"
        except requests.RequestException as e:
            if attempt == attempts:
                return False, type(e).__name__
            time.sleep(2)
    return False, "unknown"


def main():
    ap = argparse.ArgumentParser(description="Replay decoded QR JSONs to the receiver API")
    ap.add_argument("dates", nargs="+", help="date directories, e.g. 2026-08-24")
    ap.add_argument("--dry-run", action="store_true", help="show what would be sent, post nothing")
    ap.add_argument("--limit", type=int, help="stop after N sends")
    ap.add_argument("--delay", type=float, default=0.05, help="seconds between sends (default 0.05)")
    ap.add_argument("--cameras", default="LMRAB", help="camera letters to send (default LMRAB)")
    args = ap.parse_args()

    if not os.path.isdir(BASE_DIR):
        sys.exit(f"Mount not available: {BASE_DIR}")

    cameras = set(args.cameras.upper())
    session = requests.Session()
    totals = collections.Counter()

    for date in args.dates:
        entries = collect(date, cameras)
        if entries is None:
            print(f"=== {date}: no raw/ directory, skipping ===")
            continue

        groups = len({(e[2], e[4]) for e in entries})
        print(f"=== {date}: {len(entries)} decoded result(s) across {groups} take(s) ===")

        for entry in entries:
            if args.limit is not None and totals["sent"] >= args.limit:
                print(f"--- limit of {args.limit} reached ---")
                break

            if args.dry_run:
                print(f"  would send {entry[0]} {entry[1]} glosId={entry[2]} time={entry[4]}")
                totals["sent"] += 1
                continue

            ok, action = send(entry, date, session)
            if ok:
                totals["sent"] += 1
                totals[action] += 1
                if totals["sent"] % 100 == 0:
                    print(f"  ... {totals['sent']} sent "
                          f"({totals['inserted']} inserted, {totals['updated']} updated)")
            else:
                totals["failed"] += 1
                print(f"  ! {entry[1]}: {action}")
            time.sleep(args.delay)

        if args.limit is not None and totals["sent"] >= args.limit:
            break

    verb = "would send" if args.dry_run else "sent"
    print(f"\nDone. {verb}: {totals['sent']} "
          f"(inserted: {totals['inserted']}, updated: {totals['updated']}), "
          f"failed: {totals['failed']}")
    return 1 if totals["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
