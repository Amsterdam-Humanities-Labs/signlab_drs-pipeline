"""Test fixed 1440x1252 output on 3 specific videos"""
import os, sys, uuid, shutil, subprocess
sys.path.insert(0, '/Users/signlab/drs/services')
sys.path.insert(0, '/Users/signlab/drs/shared')
from crop import extract_frames, process_frames, write_video, reencode_with_ffmpeg
import cv2

BASE = '/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles'
OUT_DIR = '/Users/signlab/drs/temp/test_fixed_res'
os.makedirs(OUT_DIR, exist_ok=True)

test_files = [
    f'{BASE}/2024-11-07/post_noncropped/L20241107_6250.mp4',
    f'{BASE}/2024-11-07/post_noncropped/M20241107_6249.mp4',
    f'{BASE}/2024-11-07/post_noncropped/R20241107_7291.mp4',
]

# Also find case-insensitive matches
def find_file(path):
    d = os.path.dirname(path)
    target = os.path.basename(path).lower()
    if os.path.exists(path):
        return path
    for f in os.listdir(d):
        if f.lower() == target:
            return os.path.join(d, f)
    return None

for input_file in test_files:
    actual = find_file(input_file)
    if not actual:
        print(f"NOT FOUND: {input_file}")
        continue

    basename = os.path.basename(actual)
    print(f"\nProcessing {basename}...")

    task_dir = os.path.join(OUT_DIR, f"task_{uuid.uuid4().hex}")
    os.makedirs(task_dir, exist_ok=True)
    output_file = os.path.join(OUT_DIR, basename)

    try:
        frames_dir = os.path.join(task_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        frame_paths, _ = extract_frames(actual, frames_dir)

        cap = cv2.VideoCapture(actual)
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()

        processed_dir = os.path.join(task_dir, "processed")
        os.makedirs(processed_dir, exist_ok=True)
        processed_paths, _, head, waist, margin, _ = process_frames(frame_paths, processed_dir)

        write_video(processed_paths, output_file, fps)
        h264_file = reencode_with_ffmpeg(output_file)

        check = h264_file if h264_file and os.path.exists(h264_file) else output_file
        result = subprocess.run(
            ['/opt/homebrew/bin/ffprobe', '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height', '-of', 'csv=p=0', check],
            capture_output=True, text=True
        )
        w, h = result.stdout.strip().split(',')
        ratio = round(int(w) / int(h), 4)
        print(f"  -> {w}x{h} ratio={ratio}")
    except Exception as e:
        print(f"  -> ERROR: {e}")
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)
        for f in [output_file, os.path.splitext(output_file)[0] + '_h264' + os.path.splitext(output_file)[1]]:
            if os.path.exists(f):
                os.remove(f)

shutil.rmtree(OUT_DIR, ignore_errors=True)
