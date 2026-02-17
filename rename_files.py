import os
import sys
import re
from datetime import datetime, timedelta
from pathlib import Path

# Ensure output is flushed immediately to logs
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Path constants
BASE_DIR = Path("/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles")


def clean_rendered_filename(filename):
    """
    Clean up DaVinci Resolve's auto-appended suffixes from filename.
    DaVinci may add '_1', '_2', ' 1', ' 2' etc. before the extension.
    Example: 'L20241217_1030_1.mp4' -> 'L20241217_1030.MP4'
    Example: 'M20241217_1030 1.mp4' -> 'M20241217_1030.MP4'
    """
    # Pattern to match: base_name + optional suffix (_N or space N) + extension
    # Original filename pattern: [L|M|R]YYYYMMDD_HHMM.MP4
    match = re.match(r'^([LMR]\d{8}_\d{4})(?:_\d+| \d+)?\.mp4$', filename, re.IGNORECASE)
    if match:
        base_name = match.group(1)
        return f"{base_name}.MP4"  # Return with original .MP4 extension
    return filename  # Return unchanged if pattern doesn't match


def verify_mount_health():
    """Verify that the rclone mount is healthy and accessible"""
    mount_path = "/Users/signlab/signCollect"
    test_path = os.path.join(mount_path, "AIHR-FGW-TEST-SIGNLAB (Projectfolder)")

    try:
        if not os.path.exists(mount_path):
            print(f"Mount path does not exist: {mount_path}")
            return False

        if not os.path.exists(test_path):
            print(f"Expected directory not found in mount: {test_path}")
            return False

        try:
            contents = os.listdir(test_path)
            if not contents:
                print(f"Mount directory appears empty, may be unmounted: {test_path}")
                return False

            print(f"Mount health check passed. Found {len(contents)} items in {test_path}")
            return True

        except (OSError, PermissionError) as e:
            print(f"Mount appears unresponsive: {e}")
            return False

    except Exception as e:
        print(f"Error during mount health check: {e}")
        return False


def rename_files_in_folder(folder_path, dry_run=False):
    """Rename files with DaVinci suffixes in a folder. Returns (renamed_count, skipped_count)"""
    renamed = 0
    skipped = 0

    if not folder_path.exists():
        return renamed, skipped

    # Find all mp4 files
    for file_path in folder_path.glob("*.mp4"):
        original_name = file_path.name
        clean_name = clean_rendered_filename(original_name)

        if clean_name != original_name:
            new_path = folder_path / clean_name

            # Check if target already exists
            if new_path.exists():
                print(f"  SKIP: {original_name} -> {clean_name} (target already exists)")
                skipped += 1
                continue

            if dry_run:
                print(f"  [DRY RUN] Would rename: {original_name} -> {clean_name}")
            else:
                try:
                    file_path.rename(new_path)
                    print(f"  Renamed: {original_name} -> {clean_name}")
                    renamed += 1
                except Exception as e:
                    print(f"  ERROR renaming {original_name}: {e}")
                    skipped += 1

    # Also check for uppercase .MP4 files
    for file_path in folder_path.glob("*.MP4"):
        original_name = file_path.name
        clean_name = clean_rendered_filename(original_name)

        if clean_name != original_name:
            new_path = folder_path / clean_name

            if new_path.exists():
                print(f"  SKIP: {original_name} -> {clean_name} (target already exists)")
                skipped += 1
                continue

            if dry_run:
                print(f"  [DRY RUN] Would rename: {original_name} -> {clean_name}")
            else:
                try:
                    file_path.rename(new_path)
                    print(f"  Renamed: {original_name} -> {clean_name}")
                    renamed += 1
                except Exception as e:
                    print(f"  ERROR renaming {original_name}: {e}")
                    skipped += 1

    return renamed, skipped


def rename_all_files(days_back=2, dry_run=False):
    """Rename files with DaVinci suffixes from the past N days"""
    print(f"=== Rename Files Script Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"Scanning past {days_back} days...")
    if dry_run:
        print("*** DRY RUN MODE - No files will be renamed ***")

    # Verify mount health
    if not verify_mount_health():
        print("Mount is not healthy. Exiting.")
        return

    total_renamed = 0
    total_skipped = 0

    for day_offset in range(days_back + 1):  # Include today + past N days
        check_date = datetime.now() - timedelta(days=day_offset)
        date_str = check_date.strftime("%Y-%m-%d")

        print(f"\nProcessing {date_str}...")

        # Check post_noncropped folder
        post_noncropped_dir = BASE_DIR / date_str / "post_noncropped"
        if post_noncropped_dir.exists():
            print(f"  Checking post_noncropped...")
            renamed, skipped = rename_files_in_folder(post_noncropped_dir, dry_run)
            total_renamed += renamed
            total_skipped += skipped

        # Check post folder
        post_dir = BASE_DIR / date_str / "post"
        if post_dir.exists():
            print(f"  Checking post...")
            renamed, skipped = rename_files_in_folder(post_dir, dry_run)
            total_renamed += renamed
            total_skipped += skipped

    print(f"\n=== Rename Complete ===")
    print(f"Renamed: {total_renamed}")
    print(f"Skipped: {total_skipped}")
    print(f"=== Finished at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")


if __name__ == "__main__":
    # Parse command line arguments
    days_back = 2
    dry_run = False

    for arg in sys.argv[1:]:
        if arg == "--dry-run":
            dry_run = True
        else:
            try:
                days_back = int(arg)
            except ValueError:
                print(f"Invalid argument: {arg}")
                print("Usage: python3 rename_files.py [days_back] [--dry-run]")
                print("  days_back: Number of days to scan (default: 2)")
                print("  --dry-run: Show what would be renamed without actually renaming")
                sys.exit(1)

    rename_all_files(days_back, dry_run)
