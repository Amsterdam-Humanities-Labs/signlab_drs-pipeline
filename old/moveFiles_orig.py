import os
import shutil
import re
import time
from pathlib import Path

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

def move_files():
    source_base = "/Volumes/cacheDisk/signCollect/studioFiles"
    target_base = "/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles"
    
    if not os.path.exists(source_base):
        print(f"Source directory not found: {source_base}")
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
                
                # Move the file
                shutil.move(source_file, target_file)
                print(f"Moved: {filename} -> {date_str}/raw/")
                moved_count += 1
                
            except Exception as e:
                print(f"Error moving {filename}: {e}")
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
            time.sleep(24 * 60 * 60)  # Sleep for 24 hours (86400 seconds)
        except KeyboardInterrupt:
            print("\nService stopped by user")
            break
        except Exception as e:
            print(f"Unexpected error: {e}")
            print("Sleeping for 1 hour before retry...")
            time.sleep(60 * 60)  # Sleep for 1 hour on error
