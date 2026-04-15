#!/usr/bin/python3
"""
reprocess_videos.py - Re-process specific videos with SCALED dimensions from reference

This script processes the 10 target videos from 2025-12-01 by:
1. Extracting crop dimensions from reference video (M20251201_7047.mp4)
2. Detecting person size in each target video
3. Scaling each video so the person appears the SAME SIZE as in the reference
4. Applying the crop to produce consistent output

All videos will have the person at the same visual size as the reference.
"""

import os
import sys

# Add the services and shared directories to path
sys.path.insert(0, '/Users/signlab/drs/services')
sys.path.insert(0, '/Users/signlab/drs/shared')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # for crop_fix_request in tools/

from crop_fix_request import (
    get_reference_dimensions,
    process_with_fixed_dimensions,
    cleanup_temp_dirs,
    find_file_case_insensitive
)

from crop_fix import (
    cleanup_old_temp_files,
    cleanup_temp_by_size
)

# Target files to re-process
TARGET_FILES = [
    "M20251201_7043.mp4",
    "M20251201_7044.mp4",
    "M20251201_7045.mp4",
    "M20251201_7047.mp4",
    "M20251201_7048.mp4",
    "M20251201_7052.mp4",
    "M20251201_7053.mp4",
    "M20251201_7055.mp4",
    "M20251201_7056.mp4",
    "M20251201_7057.mp4",
]

# Reference video to extract crop dimensions from (all videos will match this zoom level)
REFERENCE_FILE = "M20251201_7047.mp4"

BASE_DIR = "/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles/2025-12-01"
TEMP_DIR = "/Users/signlab/drs/temp/"


def main():
    print("\n" + "="*70)
    print("RE-PROCESSING VIDEOS WITH FIXED DIMENSIONS FROM REFERENCE")
    print(f"Reference video: {REFERENCE_FILE}")
    print("="*70 + "\n")

    # Setup temp directory
    if os.path.exists(TEMP_DIR):
        print("Cleaning up temp directory...")
        cleanup_old_temp_files(TEMP_DIR, max_age_hours=0)
        cleanup_temp_by_size(TEMP_DIR, max_size_gb=10)
    else:
        os.makedirs(TEMP_DIR, exist_ok=True)

    source_dir = os.path.join(BASE_DIR, "post_noncropped")
    output_dir = os.path.join(BASE_DIR, "post")

    # Step 1: Get fixed crop dimensions from reference video
    print(f"\nStep 1: Extracting dimensions from reference video...")
    reference_path = find_file_case_insensitive(source_dir, REFERENCE_FILE)

    if not reference_path:
        print(f"ERROR: Reference file not found: {REFERENCE_FILE}")
        return

    print(f"Found reference file: {reference_path}")
    crop_params = get_reference_dimensions(reference_path)

    if not crop_params:
        print("ERROR: Failed to get crop parameters from reference")
        return

    # Step 2: Apply fixed dimensions to all target files
    print(f"\nStep 2: Processing {len(TARGET_FILES)} target files with fixed dimensions...")

    success_count = 0
    fail_count = 0

    for filename in TARGET_FILES:
        print(f"\n{'-'*60}")
        print(f"Processing: {filename}")
        print(f"{'-'*60}")

        # Find input file (case-insensitive)
        input_file = find_file_case_insensitive(source_dir, filename)

        if not input_file:
            print(f"WARNING: File not found: {filename}")
            fail_count += 1
            continue

        # Output file always lowercase .mp4
        output_filename = os.path.splitext(filename)[0] + ".mp4"
        output_file = os.path.join(output_dir, output_filename)

        # Delete existing h264 file if present
        h264_output = os.path.splitext(output_file)[0] + "_h264.mp4"
        if os.path.exists(h264_output):
            os.remove(h264_output)
            print(f"Removed existing: {h264_output}")

        try:
            # Process with FIXED dimensions from reference video
            if process_with_fixed_dimensions(input_file, output_file, crop_params):
                # Check if h264 output exists
                if os.path.exists(h264_output):
                    print(f"SUCCESS: {filename}")
                    success_count += 1
                else:
                    print(f"FAILED: {filename} (no h264 output)")
                    fail_count += 1
            else:
                print(f"FAILED: {filename}")
                fail_count += 1

        except Exception as e:
            print(f"ERROR: {filename} - {str(e)}")
            fail_count += 1

    # Summary
    print("\n" + "="*70)
    print("COMPLETE")
    print(f"  Reference: {REFERENCE_FILE}")
    print(f"  Success: {success_count}/{len(TARGET_FILES)}")
    print(f"  Failed:  {fail_count}/{len(TARGET_FILES)}")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
