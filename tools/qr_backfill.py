#!/usr/bin/env python3
"""
QR backfill: re-scan raw videos whose JSON is empty ([]) or missing, using a
denser frame sweep than the production scanner, and write the decoded result.

Sampling: frame 0, 30, 60, ... up to --max-frame (default 240), step 30.
Detection: same two stages as the production scanner (QReader, then
decode_partial_qr fallback).

By default it ONLY processes videos whose JSON is empty or missing; populated
JSONs ([glosId, type, time]) are left untouched. Writes locally only — does NOT
send to the API (use --send-api to also push new hits).

Usage:
    python3 tools/qr_backfill.py <raw_dir> [--step 30] [--max-frame 240]
                                 [--dry-run] [--send-api] [--limit N]
"""
import cv2
import os
import sys
import json
import re
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'qr'))
from qr_scanner_service import decode_partial_qr
from qreader import QReader

API_URL = "https://signcollect.nl/qr/qrResultReceiver.php"


def needs_fill(json_path):
    """True if JSON is missing or empty/invalid (i.e. no [glosId,type,time])."""
    if not os.path.exists(json_path):
        return True
    try:
        data = json.load(open(json_path))
    except Exception:
        return True
    return not (isinstance(data, list) and len(data) == 3)


def first_qr(video_path, qreader, step, max_frame):
    cap = cv2.VideoCapture(video_path)
    idx = 0
    hit = None
    while idx <= max_frame:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            break
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        decoded = None
        try:
            for det in qreader.detect(image=rgb):
                x1, y1, x2, y2 = det['bbox_xyxy']
                x1, y1, x2, y2 = int(x1 - 10), int(y1 - 10), int(x2 + 10), int(y2 + 10)
                crop = rgb[max(0, y1):y2, max(0, x1):x2]
                d = qreader.detect_and_decode(image=crop)
                if d and d[0]:
                    decoded = d[0]
                    break
        except Exception:
            pass
        if not decoded:
            try:
                h, w = frame.shape[:2]
                pr = decode_partial_qr(frame[int(h * 0.6):, int(w * 0.4):])
                if pr:
                    decoded = pr
            except Exception:
                pass
        if decoded:
            hit = (idx, decoded)
            break
        idx += step
    cap.release()
    return hit


def parse_name(filename):
    m = re.match(r'^([ABLMR])(\d{4})(\d{2})(\d{2})_(\d+)\.MP4$', filename, re.IGNORECASE)
    if not m:
        return None, None, None
    cam, y, mo, d, inc = m.groups()
    return cam.upper(), f"{y}-{mo}-{d}", f"{cam.upper()}{y}{mo}{d}_{inc}.wav"


def send_api(cam, wav, data, date_str):
    import requests
    glos, typ, tstr = data
    try:
        r = requests.post(API_URL, json={"camera": cam, "filename": wav, "glosId": glos,
                                         "type": typ, "time": tstr, "date": date_str}, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('raw_dir')
    ap.add_argument('--step', type=int, default=30)
    ap.add_argument('--max-frame', type=int, default=240)
    ap.add_argument('--dry-run', action='store_true', help='scan + report, do not write JSON')
    ap.add_argument('--send-api', action='store_true', help='also POST new hits to the API')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    raw = args.raw_dir
    mp4s = sorted(f for f in os.listdir(raw) if f.upper().endswith('.MP4'))
    todo = [f for f in mp4s if needs_fill(os.path.join(raw, os.path.splitext(f)[0] + '.json'))]
    if args.limit:
        todo = todo[:args.limit]

    print(f"# raw_dir: {raw}", flush=True)
    print(f"# {len(mp4s)} mp4, {len(todo)} need fill | step={args.step} max_frame={args.max_frame} "
          f"| {'DRY-RUN' if args.dry_run else 'WRITE'} {'+API' if args.send_api else ''}", flush=True)

    qreader = QReader()
    filled = still_empty = api_ok = 0
    for i, f in enumerate(todo, 1):
        jpath = os.path.join(raw, os.path.splitext(f)[0] + '.json')
        hit = first_qr(os.path.join(raw, f), qreader, args.step, args.max_frame)
        if hit:
            frame_idx, raw_decoded = hit
            try:
                data = json.loads(raw_decoded)
            except Exception:
                data = None
            if isinstance(data, list) and len(data) == 3:
                if not args.dry_run:
                    with open(jpath, 'w') as fh:
                        json.dump(data, fh)
                api = ''
                if args.send_api and not args.dry_run:
                    cam, date_str, wav = parse_name(f)
                    if cam:
                        ok = send_api(cam, wav, data, date_str)
                        api = ' API-OK' if ok else ' API-ERR'
                        api_ok += int(ok)
                filled += 1
                print(f"[{i}/{len(todo)}] {f} -> frame {frame_idx}: {data}{api}", flush=True)
                continue
        still_empty += 1
        print(f"[{i}/{len(todo)}] {f} -> still no QR (<= frame {args.max_frame})", flush=True)

    print(f"\n# DONE {os.path.basename(os.path.dirname(raw))}: filled={filled} "
          f"still_empty={still_empty}" + (f" api_ok={api_ok}" if args.send_api else ""), flush=True)


if __name__ == '__main__':
    main()
