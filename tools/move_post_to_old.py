#!/usr/bin/env python3
"""
Script to rename/move 'post' subdirectories to 'post_old'
for date-named directories in a given root between two dates.

Usage:
  python rename_post_dirs.py /path/to/root_dir

By default, start date is 2023-03-05 and end date is 2025-02-18 (inclusive).
"""
import sys
import os
import datetime
from pathlib import Path

def main(root_path: Path,
         start_date: datetime.date = datetime.date(2023, 3, 5),
         end_date: datetime.date   = datetime.date(2025, 2, 18)):
    if not root_path.is_dir():
        print(f"Error: '{root_path}' is not a directory.")
        sys.exit(1)

    for entry in sorted(root_path.iterdir()):
        if not entry.is_dir():
            continue
        # Parse directory name as date YYYY-MM-DD
        try:
            dir_date = datetime.datetime.strptime(entry.name, "%Y-%m-%d").date()
        except ValueError:
            # Skip non-date directories
            continue
        # Check if within range
        if start_date <= dir_date <= end_date:
            src = entry / 'post'
            dst = entry / 'post_old'
            if src.is_dir():
                if dst.exists():
                    print(f"Skipping {entry.name}: 'post_old' already exists.")
                else:
                    try:
                        src.rename(dst)
                        print(f"Renamed {src} -> {dst}")
                    except Exception as e:
                        print(f"Failed to rename in {entry.name}: {e}")
            else:
                print(f"No 'post' dir in {entry.name}, skipping.")

if __name__ == '__main__':
    root = Path("/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles")
    main(root)
