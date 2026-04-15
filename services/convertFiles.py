import os
import sys
import subprocess
import requests
import re
from datetime import datetime, timedelta
from pathlib import Path
import json
import time
sys.path.insert(0, '/Users/signlab/drs/shared')
from signcollect_monitor import SignCollectMonitor  # Import the monitor client

# Initialize the monitor
monitor = SignCollectMonitor(
    client_id='drs-converter',
    client_name='DRS File Converter',
    description='Video conversion and upload service',
    heartbeat_interval=3600
)

# Configuration
DEST_BASE_DIR = '/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles'
UPLOAD_URL = 'https://signcollect.nl/videoProc/upload2.php'

def upload_file_to_server(file_path):
    """Upload a single file to signcollect.nl"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    
    # Determine upload field
    ext = Path(file_path).suffix.lower()
    if ext in ['.jpg', '.jpeg', '.png']:
        field = 'thumbnail'
    elif ext in ['.mp4', '.mov']:
        field = 'video'
    else:
        raise ValueError(f"Unsupported extension: {ext}")
    
    print(f"→ uploading {file_path} as '{field}'")
    
    try:
        with open(file_path, 'rb') as f:
            files = {field: (os.path.basename(file_path), f, 'application/octet-stream')}
            headers = {'Accept': 'application/json'}
            
            response = requests.post(UPLOAD_URL, files=files, headers=headers, timeout=180)
            result = response.json()
            print(f"← {field} upload succeeded. Server replied:", result)
            return result
    except Exception as err:
        print(f"← upload error: {err}")
        raise

def get_all_date_dirs():
    """Get all date-formatted directory names from the base directory"""
    date_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}$')
    dirs = []
    if os.path.exists(DEST_BASE_DIR):
        for name in os.listdir(DEST_BASE_DIR):
            if date_pattern.match(name) and os.path.isdir(os.path.join(DEST_BASE_DIR, name)):
                dirs.append(name)
    dirs.sort()
    return dirs

def run_ffmpeg_command(command):
    """Execute FFmpeg command and return success status"""
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        return result.returncode == 0
    except Exception as e:
        print(f"FFmpeg command failed: {e}")
        return False

def get_video_duration(file_path):
    """Get video duration using ffprobe"""
    ffprobe_command = f'ffprobe -v error -select_streams v:0 -show_entries stream=duration -of default=noprint_wrappers=1:nokey=1 "{file_path}"'
    try:
        result = subprocess.run(ffprobe_command, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception as e:
        print(f"ffprobe command failed: {e}")
    return 0

def is_valid_filename(filename):
    """Check if filename follows expected pattern and doesn't contain unwanted text"""
    # Must start with L, M, R, A, or B followed by expected pattern
    pattern = r'^[LMRAB]\d{8}_\d{4}\.(?:mp4|mov|MP4|MOV)$'
    
    if not re.match(pattern, filename):
        return False
    
    # Skip files with unwanted patterns
    unwanted_patterns = ['(restored)', '(copy)', '(duplicate)']
    filename_lower = filename.lower()
    
    for pattern in unwanted_patterns:
        if pattern in filename_lower:
            return False
    
    return True

def process_directory(date_dir):
    """Process a single date directory"""
    print(f"Processing directory: {date_dir}")
    
    # Define directories
    raw_dir = os.path.join(DEST_BASE_DIR, date_dir, 'raw')
    converted_dir = os.path.join(DEST_BASE_DIR, date_dir, 'converted')
    thumbnail_dir = os.path.join(DEST_BASE_DIR, date_dir, 'thumbnails')
    
    # Check if raw directory exists
    if not os.path.exists(raw_dir):
        return
    
    # Create converted and thumbnail directories if they don't exist
    os.makedirs(converted_dir, exist_ok=True)
    os.makedirs(thumbnail_dir, exist_ok=True)
    
    # Get all video files from raw directory
    video_extensions = ['.mp4', '.mov', '.MP4', '.MOV']
    raw_files = [f for f in os.listdir(raw_dir) 
                 if any(f.lower().endswith(ext.lower()) for ext in video_extensions)
                 and is_valid_filename(f)]
    
    for filename in raw_files:
        source_path = os.path.join(raw_dir, filename)
        destination_path = os.path.join(converted_dir, filename)
        thumbnail_path = os.path.join(thumbnail_dir, f"{Path(filename).stem}.jpg")
        
        conversion_happened = False
        
        # Check if converted file already exists
        if not os.path.exists(destination_path):
            print(f"Converting: {filename}")
            
            # Convert video file
            ffmpeg_convert_command = f'/opt/homebrew/bin/ffmpeg -loglevel quiet -nostdin -i "{source_path}" -c:v libx264 -c:a aac -pix_fmt yuv420p -profile:v baseline -level 3 "{destination_path}" -n'
            
            if run_ffmpeg_command(ffmpeg_convert_command):
                print(f"✓ Conversion successful: {filename}")
                conversion_happened = True
            else:
                print(f"✗ Conversion failed: {filename}")
                continue
        
        # Only proceed with thumbnail and upload if conversion happened
        if conversion_happened:
            # Check if thumbnail exists
            if not os.path.exists(thumbnail_path):
                print(f"Generating thumbnail: {filename}")
                
                # Get video duration and calculate midpoint
                duration = get_video_duration(destination_path)
                midpoint = duration / 2 if duration > 0 else 1
                
                # Generate thumbnail
                ffmpeg_thumbnail_command = f'/opt/homebrew/bin/ffmpeg -loglevel quiet -i "{destination_path}" -ss {midpoint} -vframes 1 "{thumbnail_path}" -y'
                
                if run_ffmpeg_command(ffmpeg_thumbnail_command):
                    print(f"✓ Thumbnail generated: {thumbnail_path}")
                else:
                    print(f"✗ Thumbnail generation failed: {filename}")
                    continue
            
            # Upload files since conversion just happened
            try:
                if os.path.exists(destination_path):
                    upload_file_to_server(destination_path)
                
                if os.path.exists(thumbnail_path):
                    upload_file_to_server(thumbnail_path)
            except Exception as e:
                print(f"Upload failed for {filename}: {e}")

def scan_and_process():
    """Main function to scan directories and process files"""
    print("Starting directory scan...")
    
    # Get all date directories
    existing_dirs = get_all_date_dirs()

    print(f"Found {len(existing_dirs)} existing directories to process")
    
    # Process each directory
    for date_dir in existing_dirs:
        process_directory(date_dir)
    
    print("Directory scan completed")

def main():
    """Main loop that runs every hour"""
    # Register with monitoring system
    monitor.register()

    while True:
        try:
            # Send heartbeat at start of each cycle
            monitor.send_heartbeat()

            scan_and_process()
        except Exception as e:
            print(f"Error during processing: {e}")

        print("Sleeping for 1 hour...")
        time.sleep(900)  # Sleep for 1 hour

if __name__ == "__main__":
    main()
