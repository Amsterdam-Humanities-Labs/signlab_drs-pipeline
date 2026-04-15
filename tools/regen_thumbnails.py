"""Force reconvert videos from raw, regenerate thumbnails, and upload both for specific dates."""
import os
import re
import subprocess
from pathlib import Path
import requests

DEST_BASE_DIR = '/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles'
UPLOAD_URL = 'https://signcollect.nl/videoProc/upload2.php'
FORCE_DATES = ['2026-02-10', '2026-02-13', '2026-02-16']

def is_valid_filename(filename):
    pattern = r'^[LMRAB]\d{8}_\d{4}\.(?:mp4|mov|MP4|MOV)$'
    if not re.match(pattern, filename):
        return False
    for p in ['(restored)', '(copy)', '(duplicate)']:
        if p in filename.lower():
            return False
    return True

def run_ffmpeg(command):
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        return result.returncode == 0
    except Exception as e:
        print(f"  FFmpeg error: {e}")
        return False

def get_video_duration(file_path):
    cmd = f'/opt/homebrew/bin/ffprobe -v error -select_streams v:0 -show_entries stream=duration -of default=noprint_wrappers=1:nokey=1 "{file_path}"'
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0

def upload_file(file_path):
    ext = Path(file_path).suffix.lower()
    field = 'thumbnail' if ext in ['.jpg', '.jpeg', '.png'] else 'video'
    try:
        with open(file_path, 'rb') as f:
            files = {field: (os.path.basename(file_path), f, 'application/octet-stream')}
            response = requests.post(UPLOAD_URL, files=files, headers={'Accept': 'application/json'}, timeout=300)
            print(f"  Uploaded {field}: {response.json()}")
            return True
    except Exception as e:
        print(f"  Upload error ({field}): {e}")
        return False

def process_date(date_dir):
    raw_dir = os.path.join(DEST_BASE_DIR, date_dir, 'raw')
    converted_dir = os.path.join(DEST_BASE_DIR, date_dir, 'converted')
    thumbnail_dir = os.path.join(DEST_BASE_DIR, date_dir, 'thumbnails')

    if not os.path.exists(raw_dir):
        print(f"No raw dir for {date_dir}, skipping")
        return

    os.makedirs(converted_dir, exist_ok=True)
    os.makedirs(thumbnail_dir, exist_ok=True)

    raw_files = [f for f in os.listdir(raw_dir)
                 if f.lower().endswith(('.mp4', '.mov')) and is_valid_filename(f)]

    print(f"\n{date_dir}: {len(raw_files)} raw videos")
    converted = 0
    thumbs = 0
    uploads_vid = 0
    uploads_thumb = 0

    for filename in sorted(raw_files):
        source_path = os.path.join(raw_dir, filename)
        dest_path = os.path.join(converted_dir, filename)
        thumbnail_path = os.path.join(thumbnail_dir, f"{Path(filename).stem}.jpg")

        # Reconvert raw -> converted (overwrite with -y)
        convert_cmd = f'/opt/homebrew/bin/ffmpeg -loglevel quiet -nostdin -y -i "{source_path}" -c:v libx264 -c:a aac -pix_fmt yuv420p -profile:v baseline -level 3 "{dest_path}"'
        if run_ffmpeg(convert_cmd):
            converted += 1
        else:
            print(f"  Convert failed: {filename}")
            continue

        # Regenerate thumbnail from converted video
        duration = get_video_duration(dest_path)
        midpoint = duration / 2 if duration > 0 else 1
        thumb_cmd = f'/opt/homebrew/bin/ffmpeg -loglevel quiet -i "{dest_path}" -ss {midpoint} -vframes 1 "{thumbnail_path}" -y'
        if run_ffmpeg(thumb_cmd):
            thumbs += 1
        else:
            print(f"  Thumbnail failed: {filename}")

        # Upload both
        if os.path.exists(dest_path):
            if upload_file(dest_path):
                uploads_vid += 1

        if os.path.exists(thumbnail_path):
            if upload_file(thumbnail_path):
                uploads_thumb += 1

    print(f"\n{date_dir} summary:")
    print(f"  Converted: {converted}/{len(raw_files)}")
    print(f"  Thumbnails: {thumbs}/{len(raw_files)}")
    print(f"  Videos uploaded: {uploads_vid}")
    print(f"  Thumbnails uploaded: {uploads_thumb}")

def main():
    print(f"Force reconvert + thumbnail regen for: {FORCE_DATES}")
    for date_dir in FORCE_DATES:
        process_date(date_dir)
    print("\nDone!")

if __name__ == '__main__':
    main()
