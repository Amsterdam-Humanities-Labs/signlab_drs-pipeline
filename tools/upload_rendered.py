import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, '/Users/signlab/drs/shared')
from video_api_client import VideoAPIClient

# Ensure output is flushed immediately to logs
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Path constants
BASE_DIR = Path("/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles")
API_URL = 'https://signcollect.nl/renderServer'


def get_file_type(filename):
    """Determine file type from filename pattern [L|M|R]YYYYMMDD_HHmm.MP4"""
    first_char = filename[0].upper()
    if first_char == 'L':
        return 'l_file'
    elif first_char == 'R':
        return 'r_file'
    else:
        return 'm_file'


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


def get_rendered_files(days_back=2):
    """Get all rendered files from post_noncropped folders for the past N days"""
    all_files = []

    for day_offset in range(days_back + 1):  # Include today + past N days
        check_date = datetime.now() - timedelta(days=day_offset)
        date_str = check_date.strftime("%Y-%m-%d")

        post_noncropped_dir = BASE_DIR / date_str / "post_noncropped"

        if not post_noncropped_dir.exists():
            print(f"No post_noncropped folder for {date_str}")
            continue

        # Pattern: [L|M|R]YYYYMMDD_HHmm.MP4 (lowercase .mp4 after rendering)
        mp4_files = list(post_noncropped_dir.glob("[LMR]202*.MP4"))
        mp4_files.extend(list(post_noncropped_dir.glob("[LMR]202*.mp4")))

        if mp4_files:
            print(f"Found {len(mp4_files)} rendered files for {date_str}")
            all_files.extend(mp4_files)

    return all_files


def upload_all_rendered(days_back=2):
    """Upload rendered status for all files from the past N days"""
    print(f"=== Upload Rendered Script Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"Scanning past {days_back} days...")

    # Verify mount health
    if not verify_mount_health():
        print("Mount is not healthy. Exiting.")
        return

    # Get all rendered files
    rendered_files = get_rendered_files(days_back)

    if not rendered_files:
        print("No rendered files found.")
        return

    print(f"\nFound {len(rendered_files)} total rendered files to upload")

    # Initialize API client
    api_client = VideoAPIClient(API_URL)

    success_count = 0
    error_count = 0

    for file_path in rendered_files:
        filename = file_path.name

        try:
            file_type = get_file_type(filename)
            result = api_client.update_rendered(filename, file_type)

            if result and 'error' not in result:
                print(f"  ✓ Uploaded {filename} ({file_type})")
                success_count += 1
            else:
                print(f"  ✗ Failed {filename}: {result}")
                error_count += 1

        except Exception as e:
            print(f"  ✗ Error uploading {filename}: {e}")
            error_count += 1

    print(f"\n=== Upload Complete ===")
    print(f"Success: {success_count}")
    print(f"Errors: {error_count}")
    print(f"Total: {len(rendered_files)}")
    print(f"=== Finished at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")


if __name__ == "__main__":
    # Allow specifying days_back as command line argument
    days_back = 2
    if len(sys.argv) > 1:
        try:
            days_back = int(sys.argv[1])
        except ValueError:
            print(f"Invalid days_back argument: {sys.argv[1]}, using default of 2")

    upload_all_rendered(days_back)
