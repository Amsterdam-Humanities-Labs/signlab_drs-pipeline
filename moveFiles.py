import os
import re
import time
import subprocess
from pathlib import Path

def rsync_copy(source, destination, retries=2):
    """
    Use rsync to copy files from rclone mounted drives for better performance.
    Includes retry logic similar to the original shutil.copy2 implementation.
    """
    # Ensure destination directory exists
    dest_path = Path(destination)
    dest_dir = dest_path.parent
    if not dest_dir.exists():
        os.makedirs(dest_dir, exist_ok=True)
    
    for attempt in range(retries):
        try:
            # Build rsync command with appropriate flags
            # -a: archive mode (preserves permissions, timestamps, etc.)
            # -v: verbose
            # --progress: show progress during transfer
            cmd = ["rsync", "-av", "--progress", str(source), str(destination)]
            
            # Run rsync command
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            # If successful, return
            return True
            
        except subprocess.CalledProcessError as e:
            error_msg = f"rsync failed for {source} to {destination}"
            if e.stderr:
                error_msg += f": {e.stderr}"
            print(f"Attempt {attempt + 1}/{retries} - {error_msg}")
            
            if attempt < retries - 1:
                print(f"Retrying in 2 seconds...")
                time.sleep(2)
            else:
                print(f"All attempts failed for {source}")
                raise OSError(f"rsync failed after {retries} attempts: {error_msg}")
                
    return False

def convert_date_format(date_str):
    """Convert date from YYYYMMDD to YYYY-MM-DD format"""
    if len(date_str) == 8:
        year = date_str[:4]
        month = date_str[4:6]
        day = date_str[6:8]
        return f"{year}-{month}-{day}"
    return None

def extract_date_from_filename(filename):
    """Extract date from filename pattern like R20250527_1851.MP4"""
    pattern = r'[ABLMR](\d{8})_\d+\.'
    match = re.search(pattern, filename)
    if match:
        return convert_date_format(match.group(1))
    return None

def is_mount_available(mount_path):
    """Check if the rclone mount is available and responsive"""
    try:
        # First check if the mount point is actually mounted using system tools
        result = subprocess.run(['mount'], capture_output=True, text=True)
        mount_output = result.stdout
        
        # Check if our specific mount path appears in the mount list with FUSE
        mount_found = False
        for line in mount_output.split('\n'):
            if mount_path in line and 'fuse' in line.lower():
                mount_found = True
                break
        
        if not mount_found:
            return False
        
        # Additionally verify the mount is responsive and has expected content
        test_path = os.path.join(mount_path, "studioFiles")
        if not os.path.exists(test_path):
            return False
        
        # Try a quick directory listing and verify it's not empty (would be empty if unmounted)
        contents = os.listdir(test_path)
        if not contents:
            return False
            
        return True
        
    except (OSError, PermissionError):
        return False
    except Exception:
        return False

def move_files():
    source_base = "/Volumes/cacheDisk/signCollect/studioFiles"
    target_base = "/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles"
    
    if not os.path.exists(source_base):
        print(f"Source directory not found: {source_base}")
        return
    
    # Check if target mount is available before proceeding
    if not is_mount_available(target_base):
        print(f"Target mount not available or unresponsive: {target_base}")
        print("Skipping this cycle - rclone mount may be down")
        return
    
    moved_count = 0
    error_count = 0
    
    # Recursively scan for files
    for root, dirs, files in os.walk(source_base):
        # Only process files in 'raw' folders
        if not root.endswith('/raw'):
            continue
            
        for filename in files:
            # Extract date from filename
            date_str = extract_date_from_filename(filename)
            if not date_str:
                continue
                
            source_file = os.path.join(root, filename)
            
            # Create target directory structure
            target_dir = os.path.join(target_base, date_str, "raw")
            target_file = os.path.join(target_dir, filename)
            
            try:
                # Create target directory if it doesn't exist
                os.makedirs(target_dir, exist_ok=True)
                
                # Copy the file using rsync and then remove source (mimic move behavior)
                rsync_copy(source_file, target_file)
                os.remove(source_file)
                print(f"Moved: {filename} -> {date_str}/raw/")
                moved_count += 1
                
            except OSError as e:
                error_message = str(e)
                if "rsync failed" in error_message:
                    print(f"rsync failed for {filename}: {e}")
                    # Check if it's a mount issue
                    if not is_mount_available(target_base):
                        print(f"Mount appears to be down during operation. Stopping batch.")
                        break
                else:
                    print(f"File system error moving {filename}: {e}")
                error_count += 1
            except Exception as e:
                print(f"Unexpected error moving {filename}: {e}")
                error_count += 1
    
    print(f"\nSummary: {moved_count} files moved, {error_count} errors")

if __name__ == "__main__":
    print("Starting moveFiles service - will run every 24 hours")
    while True:
        try:
            print(f"\n=== Starting file move operation at {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
            move_files()
            print(f"=== File move operation completed at {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
            print("Sleeping for 24 hours until next run...")
            time.sleep(3 * 60 * 60)  # Sleep for 24 hours (86400 seconds)
        except KeyboardInterrupt:
            print("\nService stopped by user")
            break
        except Exception as e:
            print(f"Unexpected error: {e}")
            print("Sleeping for 1 hour before retry...")
            time.sleep(60 * 60)  # Sleep for 1 hour on error
