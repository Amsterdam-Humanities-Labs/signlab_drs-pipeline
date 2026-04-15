"""Reprocess ALL videos from all_videos.json with fixed 1440x1252 resolution.
Finds source files in post_noncropped, reprocesses (overwrites) and uploads.
Logs failures to reprocess_report.json."""
import os
import json
import sys
import uuid
import shutil
import subprocess
import requests
from pathlib import Path
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, '/Users/signlab/drs/services')
sys.path.insert(0, '/Users/signlab/drs/shared')
from crop import process_video_file, cleanup_temp_dirs

BASE_DIR = '/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles'
JSON_URL = 'https://signcollect.nl/zin/znn_videos/all_videos.json'
TEMP_DIR = '/Users/signlab/drs/temp/'
REPORT_PATH = '/Users/signlab/drs/reprocess_all_report.json'
MAX_WORKERS = 5

def download_json():
    print("Downloading all_videos.json...")
    resp = requests.get(JSON_URL, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    print(f"Total sentences: {data['total_sentences']}, Total videos: {data['total_videos_checked']}")
    return data

def extract_filenames(data):
    """Extract unique filenames with date folders"""
    files = {}
    for s in data['sentences']:
        for cam in ['left', 'center', 'right']:
            v = s.get(cam)
            if v and v.get('filename'):
                fname = v['filename']
                date_part = fname[1:9]
                date_str = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}"
                files[fname] = date_str
    return files

def find_source_file(filename, date_str):
    input_folder = os.path.join(BASE_DIR, date_str, 'post_noncropped')
    if not os.path.isdir(input_folder):
        return None
    path = os.path.join(input_folder, filename)
    if os.path.exists(path):
        return path
    stem = Path(filename).stem
    for f in os.listdir(input_folder):
        if f.lower() == filename.lower() or Path(f).stem.lower() == stem.lower():
            return os.path.join(input_folder, f)
    return None

def get_dimensions(filepath):
    result = subprocess.run(
        ['/opt/homebrew/bin/ffprobe', '-v', 'error', '-select_streams', 'v:0',
         '-show_entries', 'stream=width,height', '-of', 'csv=p=0', filepath],
        capture_output=True, text=True
    )
    if result.returncode == 0 and result.stdout.strip():
        parts = result.stdout.strip().split(',')
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    return None, None

def check_ratio(filename, date_str):
    output_folder = os.path.join(BASE_DIR, date_str, 'post')
    if not os.path.isdir(output_folder):
        return None, None, None, False
    stem = Path(filename).stem
    for f in os.listdir(output_folder):
        f_stem = Path(f).stem
        if f_stem.lower() == f"{stem.lower()}_h264":
            h264_path = os.path.join(output_folder, f)
            w, h = get_dimensions(h264_path)
            if w and h:
                return w, h, w/h, (w == 1440 and h == 1252)
            return None, None, None, False
    return None, None, None, False

def main():
    data = download_json()
    files = extract_filenames(data)
    print(f"Unique files to reprocess: {len(files)}")

    tasks = []
    missing = []
    for filename, date_str in sorted(files.items()):
        source = find_source_file(filename, date_str)
        if source:
            output_folder = os.path.join(BASE_DIR, date_str, 'post')
            os.makedirs(output_folder, exist_ok=True)
            output_file = os.path.join(output_folder, os.path.basename(source))
            tasks.append((source, output_file, filename, date_str))
        else:
            missing.append((filename, date_str))

    print(f"Found source files: {len(tasks)}")
    if missing:
        print(f"Missing source files: {len(missing)}")
        for fname, dstr in missing[:10]:
            print(f"  {dstr}/post_noncropped/{fname}")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")

    if not tasks:
        print("No tasks to process.")
        return

    print(f"\nReprocessing {len(tasks)} videos with {MAX_WORKERS} workers...")
    completed = 0
    failed = 0
    process_errors = []

    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for source, output_file, filename, date_str in tasks:
            task_temp_dir = os.path.join(TEMP_DIR, f"reprocess_{uuid.uuid4().hex}")
            os.makedirs(task_temp_dir, exist_ok=True)
            future = executor.submit(process_video_file, source, output_file, task_temp_dir)
            futures[future] = (filename, date_str, task_temp_dir)

        for future in as_completed(futures):
            filename, date_str, temp_dir = futures[future]
            try:
                future.result()
                completed += 1
                if completed % 100 == 0:
                    print(f"Progress: {completed}/{len(tasks)} completed, {failed} failed")
            except Exception as e:
                failed += 1
                process_errors.append({
                    "filename": filename,
                    "date": date_str,
                    "error": str(e)
                })
                print(f"FAILED {filename}: {e}")
            finally:
                if os.path.exists(temp_dir):
                    cleanup_temp_dirs([temp_dir])

    print(f"\nProcessing done. Completed: {completed}, Failed: {failed}")

    # Verify ratios
    print("\nVerifying output dimensions...")
    ratio_failures = []
    ratio_passes = 0

    for source, output_file, filename, date_str in tasks:
        w, h, ratio, passed = check_ratio(filename, date_str)
        if passed:
            ratio_passes += 1
        else:
            ratio_failures.append({
                "filename": filename,
                "date": date_str,
                "width": w,
                "height": h,
                "actual_ratio": round(ratio, 4) if ratio else None
            })

    print(f"Dimension check: {ratio_passes} passed (1440x1252), {len(ratio_failures)} failed")

    report = {
        "generated_at": datetime.now().isoformat(),
        "total_files": len(tasks),
        "missing_source": len(missing),
        "processing_completed": completed,
        "processing_failed": failed,
        "dimension_passed": ratio_passes,
        "dimension_failed": len(ratio_failures),
        "dimension_failures": ratio_failures,
        "process_errors": process_errors,
        "missing_files": [{"filename": f, "date": d} for f, d in missing]
    }

    with open(REPORT_PATH, 'w') as f:
        json.dump(report, f, indent=2)
    print(f"\nReport saved to: {REPORT_PATH}")

if __name__ == '__main__':
    main()
