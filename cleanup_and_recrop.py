#!/usr/bin/env python3
"""
Cleanup script: deletes already-uploaded files from post and post_noncropped,
then re-crops preserved post_noncropped files (>= 2025-12-10) using crop_znn.py.

Phase 1: Delete all matching files from all directories
Phase 2: Verify all directories are clean
Phase 3: Re-crop all preserved directories
"""

import os
import sys
import json
import time
import requests
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
import uuid

sys.path.insert(0, '/Users/signlab/drs')
from crop_znn import process_video_file, cleanup_temp_dirs

BASE_DIR = '/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles'
API_URL = 'https://api.signcollect.nl/list/zin/videos'
CUTOFF_DATE = datetime(2025, 12, 10)
TEMP_DIR = '/Users/signlab/drs/temp/'

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)


def fetch_api_basenames():
    """Fetch all video basenames from the signcollect API"""
    print("Fetching video list from API...")
    basenames = set()
    for page in range(1, 21):
        try:
            resp = requests.get(f'{API_URL}?page={page}', verify=False)
            data = resp.json()
            if not data.get('success'):
                continue
            for entry in data.get('data', []):
                for video_set in entry.get('videos', []):
                    for key in ['left', 'center', 'right']:
                        url = video_set.get(key, '')
                        if url:
                            filename = url.split('/')[-1]
                            base = os.path.splitext(filename)[0].lower()
                            basenames.add(base)
        except Exception as e:
            print(f"Error fetching page {page}: {e}")
    print(f"Found {len(basenames)} unique video basenames in API")
    return basenames


def get_date_dirs():
    """Get all valid date directories sorted"""
    dirs = []
    for subdir in sorted(os.listdir(BASE_DIR)):
        date_dir = os.path.join(BASE_DIR, subdir)
        if not os.path.isdir(date_dir):
            continue
        try:
            dir_date = datetime.strptime(subdir, '%Y-%m-%d')
            dirs.append((subdir, dir_date, date_dir))
        except ValueError:
            continue
    return dirs


def phase_delete(date_dirs, api_basenames):
    """Phase 1: Delete all matching files from all directories"""
    total_post = 0
    total_nc = 0

    for subdir, dir_date, date_dir in date_dirs:
        post_dir = os.path.join(date_dir, 'post')
        nc_dir = os.path.join(date_dir, 'post_noncropped')

        # Delete from post (all dates)
        if os.path.isdir(post_dir):
            deleted = 0
            for fn in os.listdir(post_dir):
                base = os.path.splitext(fn)[0].lower().replace('_h264', '')
                if base in api_basenames:
                    try:
                        os.remove(os.path.join(post_dir, fn))
                        deleted += 1
                    except Exception as e:
                        print(f"  Error deleting post/{fn}: {e}")
            total_post += deleted
            if deleted > 0:
                print(f"  {subdir}/post: deleted {deleted} files")

        # Delete from post_noncropped (only before cutoff)
        if os.path.isdir(nc_dir) and dir_date < CUTOFF_DATE:
            deleted = 0
            for fn in os.listdir(nc_dir):
                base = os.path.splitext(fn)[0].lower()
                if base in api_basenames:
                    try:
                        os.remove(os.path.join(nc_dir, fn))
                        deleted += 1
                    except Exception as e:
                        print(f"  Error deleting post_noncropped/{fn}: {e}")
            total_nc += deleted
            if deleted > 0:
                print(f"  {subdir}/post_noncropped: deleted {deleted} files")

    print(f"\nDeletion totals: {total_post} post files, {total_nc} post_noncropped files")
    return total_post, total_nc


def phase_verify(date_dirs, api_basenames):
    """Phase 2: Verify all post directories for recrop dates are clean of API files"""
    all_clean = True
    for subdir, dir_date, date_dir in date_dirs:
        if dir_date < CUTOFF_DATE:
            continue

        post_dir = os.path.join(date_dir, 'post')
        if not os.path.isdir(post_dir):
            continue

        remaining = 0
        for fn in os.listdir(post_dir):
            base = os.path.splitext(fn)[0].lower().replace('_h264', '')
            if base in api_basenames:
                remaining += 1

        if remaining > 0:
            print(f"  WARNING: {subdir}/post still has {remaining} API files remaining")
            all_clean = False
        else:
            print(f"  {subdir}/post: clean")

    return all_clean


def phase_recrop(date_dirs):
    """Phase 3: Re-crop all preserved post_noncropped files"""
    # Collect all tasks across all directories
    all_tasks = []

    for subdir, dir_date, date_dir in date_dirs:
        if dir_date < CUTOFF_DATE:
            continue

        nc_dir = os.path.join(date_dir, 'post_noncropped')
        post_dir = os.path.join(date_dir, 'post')

        if not os.path.isdir(nc_dir):
            continue

        os.makedirs(post_dir, exist_ok=True)

        for fn in sorted(os.listdir(nc_dir)):
            if not fn.lower().endswith(('.mp4', '.mov', '.avi')):
                continue

            error_json = os.path.splitext(os.path.join(nc_dir, fn))[0] + "_error.json"
            if os.path.exists(error_json):
                continue

            file_parts = os.path.splitext(fn)
            h264_fn = f"{file_parts[0]}_h264{file_parts[1]}"
            if os.path.exists(os.path.join(post_dir, h264_fn)):
                continue

            input_file = os.path.join(nc_dir, fn)
            output_file = os.path.join(post_dir, fn)
            all_tasks.append((input_file, output_file, subdir))

    if not all_tasks:
        print("No files to re-crop.")
        return

    print(f"Re-cropping {len(all_tasks)} files across {len(set(t[2] for t in all_tasks))} directories...")

    done = 0
    failed = 0

    with ProcessPoolExecutor(max_workers=5) as executor:
        futures = {}
        for input_file, output_file, subdir in all_tasks:
            task_temp_dir = os.path.join(TEMP_DIR, f"task_{uuid.uuid4().hex}")
            os.makedirs(task_temp_dir, exist_ok=True)
            future = executor.submit(process_video_file, input_file, output_file, task_temp_dir)
            futures[future] = (input_file, task_temp_dir)

        for future in as_completed(futures):
            input_file, task_temp_dir = futures[future]
            try:
                future.result()
                done += 1
                print(f"  [{done + failed}/{len(all_tasks)}] Done: {os.path.basename(input_file)}")
            except Exception as e:
                failed += 1
                print(f"  [{done + failed}/{len(all_tasks)}] Failed: {os.path.basename(input_file)}: {e}")
            finally:
                if os.path.exists(task_temp_dir):
                    cleanup_temp_dirs([task_temp_dir])

    print(f"\nRecrop complete: {done} done, {failed} failed out of {len(all_tasks)}")


if __name__ == '__main__':
    start_time = time.time()
    print(f"=== Cleanup & Re-crop Script ===")
    print(f"Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Preserving post_noncropped from {CUTOFF_DATE.strftime('%Y-%m-%d')} onwards")
    print()

    os.makedirs(TEMP_DIR, exist_ok=True)

    api_basenames = fetch_api_basenames()
    date_dirs = get_date_dirs()

    # Phase 1: Delete
    print("\n=== PHASE 1: Deleting uploaded files ===")
    phase_delete(date_dirs, api_basenames)

    # Phase 2: Verify
    print("\n=== PHASE 2: Verifying post directories are clean ===")
    all_clean = phase_verify(date_dirs, api_basenames)

    if not all_clean:
        print("\nSome directories still have API files. Recrop will NOT start.")
        print("Re-run this script to retry.")
        sys.exit(1)

    # Phase 3: Recrop
    print("\n=== PHASE 3: Re-cropping with new 1.15:1 ratio ===")
    phase_recrop(date_dirs)

    elapsed = time.time() - start_time
    print(f"\n=== All done in {elapsed/3600:.1f} hours ===")
