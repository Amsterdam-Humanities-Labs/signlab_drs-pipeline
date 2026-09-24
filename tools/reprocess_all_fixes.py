#!/usr/bin/env python3
"""
Reprocess all videos from crop_fixes API with the updated 1:1.15 ratio enforcement.
Processes all files (both resolved and unresolved) to ensure consistent aspect ratios.
"""

import os
import sys
import json
import uuid
import time
import glob
import requests
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

# Ensure output is flushed immediately
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

sys.path.insert(0, '/Users/signlab/drs/services')
sys.path.insert(0, '/Users/signlab/drs/shared')
from crop_fix import process_video_file, cleanup_temp_dirs, cleanup_old_temp_files, cleanup_temp_by_size
from server_config import server_url, videofix_headers

BASE_DIR = Path("/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles")
TEMP_BASE = "/Users/signlab/drs/temp/"
MAX_WORKERS = 5


def fetch_all_files():
    """Fetch ALL video files from crop_fixes API (both resolved and unresolved)."""
    api_url = server_url("videoFix/crop_fixes.json")

    try:
        print(f"Fetching all fixes from {api_url}")
        response = requests.get(api_url, headers=videofix_headers(), verify=False, timeout=30)
        response.raise_for_status()
        data = response.json()

        all_files = []
        for fix in data.get("fixes", []):
            m_file = fix.get("m_file")
            for file_entry in fix.get("files", []):
                all_files.append({
                    "m_file": m_file,
                    "file": file_entry.get("file"),
                    "type": file_entry.get("type"),
                    "status": file_entry.get("status"),
                })

        print(f"Found {len(all_files)} total file(s) across {len(data.get('fixes', []))} fix entries")
        return all_files
    except Exception as e:
        print(f"Failed to fetch fixes: {e}")
        return []


def find_video_paths(filename_wav):
    """Find input and output paths for a video file."""
    filename_without_ext = os.path.splitext(filename_wav)[0]
    mp4_filename = f"{filename_without_ext}.mp4"

    # Parse date from filename: M20251201_7047 -> 2025-12-01
    try:
        date_part = filename_without_ext[1:9]
        year = date_part[0:4]
        month = date_part[4:6]
        day = date_part[6:8]
        folder_date = f"{year}-{month}-{day}"
    except (IndexError, ValueError) as e:
        print(f"  Could not parse date from {filename_without_ext}: {e}")
        return None, None

    date_dir = os.path.join(str(BASE_DIR), folder_date)
    input_path = os.path.join(date_dir, "post_noncropped", mp4_filename)
    output_path = os.path.join(date_dir, "post", mp4_filename)

    if not os.path.exists(input_path):
        # Try glob fallback
        search_pattern = os.path.join(str(BASE_DIR), "*", "post_noncropped", mp4_filename)
        matches = glob.glob(search_pattern)
        if matches:
            input_path = matches[0]
            date_dir = os.path.dirname(os.path.dirname(input_path))
            output_path = os.path.join(date_dir, "post", mp4_filename)
        else:
            return None, None

    return input_path, output_path


def cleanup_existing_outputs(output_path):
    """Delete existing output files."""
    if not output_path:
        return

    base_path = os.path.splitext(output_path)[0]
    extension = os.path.splitext(output_path)[1]

    # Also check for case-insensitive matches (e.g., .MP4 vs .mp4)
    output_dir = os.path.dirname(output_path)
    basename_no_ext = os.path.splitext(os.path.basename(output_path))[0]

    files_to_delete = [
        output_path,
        f"{base_path}_h264{extension}",
        f"{base_path}_h264.mp4",
        f"{base_path}_error.json",
    ]

    # Also check for .MP4 variant
    if extension.lower() == '.mp4':
        files_to_delete.append(os.path.join(output_dir, f"{basename_no_ext}.MP4"))
        files_to_delete.append(os.path.join(output_dir, f"{basename_no_ext}_h264.MP4"))

    # Check input dir for error JSON too
    input_dir = os.path.dirname(output_path).replace("/post", "/post_noncropped")
    files_to_delete.append(os.path.join(input_dir, f"{basename_no_ext}_error.json"))

    for file_path in files_to_delete:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                print(f"    Deleted: {os.path.basename(file_path)}")
            except OSError as e:
                print(f"    WARNING: Could not delete {file_path}: {e}")


def process_single(args):
    """Wrapper for process_video_file that handles cleanup."""
    input_file, output_file, temp_dir = args
    try:
        process_video_file(input_file, output_file, temp_dir)
        # Check if h264 output exists
        base_path = os.path.splitext(output_file)[0]
        h264_output = f"{base_path}_h264{os.path.splitext(output_file)[1]}"
        if os.path.exists(h264_output):
            return True, os.path.basename(input_file)
        else:
            return False, os.path.basename(input_file)
    except Exception as e:
        return False, f"{os.path.basename(input_file)}: {e}"


def main():
    # Clean up temp directory
    print("Cleaning up temp directory...")
    os.makedirs(TEMP_BASE, exist_ok=True)
    cleanup_old_temp_files(TEMP_BASE, max_age_hours=0)
    cleanup_temp_by_size(TEMP_BASE, max_size_gb=10)

    # Fetch all files
    all_files = fetch_all_files()
    if not all_files:
        print("No files found. Exiting.")
        return

    # Build task list
    tasks = []
    skipped = 0
    not_found = 0

    for file_entry in all_files:
        filename = file_entry.get("file")
        if not filename:
            skipped += 1
            continue

        input_path, output_path = find_video_paths(filename)
        if input_path is None:
            not_found += 1
            continue

        # Clean up existing outputs
        cleanup_existing_outputs(output_path)

        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        temp_dir = os.path.join(TEMP_BASE, f"fix_{uuid.uuid4().hex}")
        os.makedirs(temp_dir, exist_ok=True)

        tasks.append((input_path, output_path, temp_dir))

    print(f"\nTask summary:")
    print(f"  Total files from API: {len(all_files)}")
    print(f"  Files to process: {len(tasks)}")
    print(f"  Skipped (no filename): {skipped}")
    print(f"  Not found on disk: {not_found}")
    print(f"  Workers: {MAX_WORKERS}")
    print()

    if not tasks:
        print("No tasks to process.")
        return

    # Process in batches
    success_count = 0
    fail_count = 0
    start_time = time.time()

    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for task in tasks:
            future = executor.submit(process_single, task)
            futures[future] = task

        for i, future in enumerate(as_completed(futures.keys()), 1):
            task = futures[future]
            try:
                success, info = future.result()
                if success:
                    success_count += 1
                    status = "OK"
                else:
                    fail_count += 1
                    status = "FAIL"
                elapsed = time.time() - start_time
                avg_per_file = elapsed / i
                remaining = avg_per_file * (len(tasks) - i)
                print(f"[{i}/{len(tasks)}] {status}: {info} "
                      f"(elapsed: {elapsed/60:.1f}min, est remaining: {remaining/60:.1f}min)")
            except Exception as e:
                fail_count += 1
                print(f"[{i}/{len(tasks)}] ERROR: {os.path.basename(task[0])}: {e}")
            finally:
                # Extra cleanup
                temp_dir = task[2]
                if os.path.exists(temp_dir):
                    cleanup_temp_dirs([temp_dir])

            # Periodic temp cleanup
            if i % 20 == 0:
                cleanup_old_temp_files(TEMP_BASE, max_age_hours=0.5)
                cleanup_temp_by_size(TEMP_BASE, max_size_gb=10)

    total_time = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"COMPLETE")
    print(f"  Processed: {len(tasks)} files")
    print(f"  Success: {success_count}")
    print(f"  Failed: {fail_count}")
    print(f"  Total time: {total_time/60:.1f} minutes")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
