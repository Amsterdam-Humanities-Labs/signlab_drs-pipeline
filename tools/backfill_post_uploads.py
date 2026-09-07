#!/usr/bin/env python3
"""
Backfill post uploads: re-send cropped videos and thumbnails that were produced
while videoProc/upload_post.php was missing from the webserver and therefore
never arrived.

crop.py uploads each file once, at crop time. A failed upload is logged and never
retried, so everything cropped during the outage stays local forever - crop only
uploads files it is cropping now, and it skips anything already cropped.

This walks the finished post/ and thumbnails/ directories, asks
media.signcollect.nl which files it already holds, and POSTs only the missing
ones. media.signcollect.nl serves the same directory upload_post.php writes into,
so a HEAD there is an accurate presence check and nothing is sent twice.

Safe to run while DaVinci and crop are still working: it only reads files that are
already finished, and never deletes or rewrites anything locally.

Note that upload_post.php stores the file but does not set post_processed, so this
restores the videos on the webserver - it does not move the studioIndex counters.

Use --force to re-send files the server already has, for when a stored copy is
wrong or truncated rather than missing. It re-uploads everything for the dates
given, so prefer the default (skip-if-present) unless you actually need to
overwrite.

Usage:
    python3 tools/backfill_post_uploads.py 2026-08-17 [2026-08-18 ...]
                                           [--dry-run] [--limit N] [--delay 0.2]
                                           [--force] [--videos-only | --thumbs-only]
"""
import os
import sys
import time
import argparse
import requests
import urllib3

# crop.py posts with verify=False; match it so this behaves like the real client.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = "/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles"
UPLOAD_URL = "https://signcollect.nl/videoProc/upload_post.php"
MEDIA_URL = "https://media.signcollect.nl"


def server_name(filename):
    """Map a local filename to the name upload_post.php stores it under.

    crop.py sends the re-encoded copy (X_h264.MP4); the served file drops the
    _h264 marker and lowercases the extension.
    """
    stem, ext = os.path.splitext(filename)
    if stem.endswith("_h264"):
        stem = stem[: -len("_h264")]
    return stem + ext.lower()


def collect_candidates(date):
    """Return [(local_path, server_name, form_field)] for one date directory."""
    candidates = []

    post_dir = os.path.join(BASE_DIR, date, "post")
    if os.path.isdir(post_dir):
        for name in sorted(os.listdir(post_dir)):
            # Only the _h264 copies are what crop uploads; the plain .MP4 next to
            # them is the intermediate and was never sent.
            if name.lower().endswith(".mp4") and os.path.splitext(name)[0].endswith("_h264"):
                candidates.append((os.path.join(post_dir, name), server_name(name), "video"))

    thumb_dir = os.path.join(BASE_DIR, date, "thumbnails")
    if os.path.isdir(thumb_dir):
        for name in sorted(os.listdir(thumb_dir)):
            if name.lower().endswith(".jpg"):
                candidates.append((os.path.join(thumb_dir, name), server_name(name), "thumbnail"))

    return candidates


def already_on_server(name, session):
    """True if media.signcollect.nl already serves this file.

    On a network error we return True so the file is left alone rather than
    re-uploaded blindly - the next run will pick it up.
    """
    try:
        r = session.head(f"{MEDIA_URL}/{name}", timeout=30, verify=False, allow_redirects=True)
        return r.status_code == 200
    except requests.RequestException as e:
        print(f"  ? {name}: presence check failed ({e}), skipping this run")
        return True


# upload_post.php answers 200 even when it discards the file (it just echoes a
# debug dump), so status alone is not proof of success - crop.py trusting the
# status is exactly how silently dropped thumbnails went unnoticed. Require the
# server to say it stored the file rather than blacklisting failure text, so a
# future change to the endpoint under-reports success instead of losing files.
SUCCESS_MARKERS = ("uploaded successfully", '"success":true')


def upload(path, name, field, session, attempts=2):
    """POST one file. Returns True only if the server confirms it stored the file."""
    mime = "image/jpeg" if field == "thumbnail" else "video/mp4"
    for attempt in range(1, attempts + 1):
        try:
            with open(path, "rb") as fh:
                files = {field: (os.path.basename(path), fh, mime)}
                r = session.post(UPLOAD_URL, files=files, timeout=300, verify=False)
            if r.status_code == 200:
                body = r.text[:500]
                if any(m.lower() in body.lower() for m in SUCCESS_MARKERS):
                    return True
                print(f"  ! {name}: 200 but not confirmed stored: {body[:90].strip()}")
                return False
            print(f"  ! {name}: HTTP {r.status_code} {r.text[:120]}")
        except requests.RequestException as e:
            print(f"  ! {name}: {type(e).__name__} {e}")
        if attempt < attempts:
            time.sleep(2)
    return False


def main():
    ap = argparse.ArgumentParser(description="Re-send post videos/thumbnails the webserver is missing")
    ap.add_argument("dates", nargs="+", help="date directories, e.g. 2026-08-17")
    ap.add_argument("--dry-run", action="store_true", help="report what would be sent, upload nothing")
    ap.add_argument("--limit", type=int, help="stop after N uploads (per run, across all dates)")
    ap.add_argument("--delay", type=float, default=0.2, help="seconds between uploads (default 0.2)")
    ap.add_argument("--force", action="store_true",
                    help="re-send even if the server already has the file (overwrites it)")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--videos-only", action="store_true", help="send only post/ videos")
    group.add_argument("--thumbs-only", action="store_true", help="send only thumbnails/")
    args = ap.parse_args()

    if not os.path.isdir(BASE_DIR):
        sys.exit(f"Mount not available: {BASE_DIR}")

    session = requests.Session()
    totals = {"present": 0, "sent": 0, "failed": 0}

    for date in args.dates:
        date_dir = os.path.join(BASE_DIR, date)
        if not os.path.isdir(date_dir):
            print(f"=== {date}: no such directory, skipping ===")
            continue

        candidates = collect_candidates(date)
        if args.videos_only:
            candidates = [c for c in candidates if c[2] == "video"]
        elif args.thumbs_only:
            candidates = [c for c in candidates if c[2] == "thumbnail"]

        mode = " (force: re-sending files the server already has)" if args.force else ""
        print(f"=== {date}: {len(candidates)} local file(s) to check{mode} ===")

        for path, name, field in candidates:
            if args.limit is not None and totals["sent"] >= args.limit:
                print(f"--- limit of {args.limit} upload(s) reached ---")
                break

            if not args.force and already_on_server(name, session):
                totals["present"] += 1
                continue

            if args.dry_run:
                size = os.path.getsize(path)
                print(f"  would send {name} ({field}, {size} bytes)")
                totals["sent"] += 1
                continue

            if upload(path, name, field, session):
                totals["sent"] += 1
                print(f"  + {name} ({totals['sent']} sent)")
                time.sleep(args.delay)
            else:
                totals["failed"] += 1

        if args.limit is not None and totals["sent"] >= args.limit:
            break

    verb = "would send" if args.dry_run else "sent"
    print(f"\nDone. already on server: {totals['present']}, {verb}: {totals['sent']}, failed: {totals['failed']}")
    return 1 if totals["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
