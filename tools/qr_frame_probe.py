#!/usr/bin/env python3
"""
QR frame probe: for each raw video, scan frame-by-frame and report the FIRST
frame index at which the QR code decodes. Uses the same two-stage detection as
the production scanner (QReader, then decode_partial_qr fallback).

Usage:
    python3 tools/qr_frame_probe.py <raw_dir> [--limit N] [--step S] [--max-frame F] [--glob PAT]

Prints one line per video: <filename> <frame|NONE> <decoded-or-empty>
"""
import cv2
import os
import sys
import json
import argparse
import fnmatch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'qr'))
# Reuse the exact partial-decode logic from the service
from qr_scanner_service import decode_partial_qr
from qreader import QReader


def first_qr_frame(video_path, qreader, step, max_frame):
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    frame_idx = 0
    result = (None, None, total, fps)
    while frame_idx <= max_frame:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            break
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        decoded = None
        # Stage 1: QReader detect + decode
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
        # Stage 2: partial reconstruction on bottom-right region
        if not decoded:
            try:
                h, w = frame.shape[:2]
                crop = frame[int(h * 0.6):, int(w * 0.4):]
                pr = decode_partial_qr(crop)
                if pr:
                    decoded = pr
            except Exception:
                pass
        if decoded:
            result = (frame_idx, decoded, total, fps)
            break
        frame_idx += step
    cap.release()
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('raw_dir')
    ap.add_argument('--limit', type=int, default=8)
    ap.add_argument('--step', type=int, default=5, help='frame step (default 5)')
    ap.add_argument('--max-frame', type=int, default=200)
    ap.add_argument('--glob', default='*.MP4')
    args = ap.parse_args()

    files = sorted(f for f in os.listdir(args.raw_dir)
                   if fnmatch.fnmatch(f, args.glob) and f.upper().endswith('.MP4'))
    if args.limit:
        files = files[:args.limit]

    qreader = QReader()
    print(f"# scanning {len(files)} videos, step={args.step}, max_frame={args.max_frame}")
    print(f"# {'file':28} {'frame':>6} {'fps':>5} {'total':>6}  decoded")
    hits = []
    for f in files:
        frame_idx, decoded, total, fps = first_qr_frame(
            os.path.join(args.raw_dir, f), qreader, args.step, args.max_frame)
        shown = (decoded[:40] if decoded else '')
        print(f"{f:28} {str(frame_idx) if frame_idx is not None else 'NONE':>6} "
              f"{fps:5.1f} {total:>6}  {shown}", flush=True)
        if frame_idx is not None:
            hits.append(frame_idx)

    if hits:
        hits.sort()
        print(f"\n# first-decode frame: min={hits[0]} max={hits[-1]} "
              f"median={hits[len(hits)//2]} (n={len(hits)})")


if __name__ == '__main__':
    main()
